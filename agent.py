"""
Contributor Agent — the Python sidecar process.

Connects to the coordinator via WebSocket, sends heartbeats every 5 seconds,
receives job assignments, executes scripts inside Docker containers, and
streams stdout back to the coordinator line-by-line.
"""

import asyncio
import json
import os
import sys
import uuid
import tempfile
import subprocess
import platform
import time
import shutil

import psutil
import websockets

COORDINATOR_WS = os.environ.get(
    "COORDINATOR_WS", "ws://localhost:8000/ws/contributor"
)

NODE_ID = str(uuid.uuid4())

MAX_CPUS = float(os.environ.get("CONTRIB_MAX_CPUS", "2.0"))
MAX_RAM_GB = int(os.environ.get("CONTRIB_MAX_RAM_GB", "4"))
MAX_GPU_VRAM_MB = int(os.environ.get("CONTRIB_MAX_GPU_VRAM_MB", "0"))


PREBAKED_GPU_PACKAGES = {"torch", "torchvision", "torchaudio"}

def filter_requirements(requirements: str) -> str:
    """Strip packages already pre-baked in the GPU image."""
    lines = []
    for line in requirements.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pkg = stripped.split("==")[0].split(">=")[0].split("<=")[0].split("[")[0].lower().strip()
        if pkg not in PREBAKED_GPU_PACKAGES:
            lines.append(stripped)
    return "\n".join(lines)

def detect_gpu():
    """Returns GPU info dict if NVIDIA GPU available, else None."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits"
            ],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None
        parts = [p.strip() for p in result.stdout.strip().split(",")]
        return {
            "name": parts[0],
            "vram_total_mb": int(parts[1]),
            "vram_free_mb": int(parts[2]),
            "gpu_utilization": int(parts[3]),
        }
    except Exception:
        return None

GPU_INFO = detect_gpu()
HAS_GPU = GPU_INFO is not None
print(f"[agent] GPU detected: {GPU_INFO['name'] if HAS_GPU else 'None'}")



async def send_heartbeats(ws):
    """Send CPU/RAM stats every 5 seconds."""
    while True:
        cpu_percent = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory()
        ram_free_gb = round(ram.available / (1024 ** 3), 2)
        
        gpu_info = detect_gpu() if HAS_GPU else None

        heartbeat = {
            "type": "heartbeat",
            "node_id": NODE_ID,
            "cpu_free": cpu_percent,
            "ram_free": ram_free_gb,
            "max_cpus": MAX_CPUS,
            "max_ram_gb": MAX_RAM_GB,
            "has_gpu": HAS_GPU and MAX_GPU_VRAM_MB > 0,
            "gpu_name": gpu_info["name"] if gpu_info else None,
            "gpu_vram_free_mb": gpu_info["vram_free_mb"] if gpu_info else 0,
            "gpu_vram_total_mb": gpu_info["vram_total_mb"] if gpu_info else 0,
            "gpu_utilization": gpu_info["gpu_utilization"] if gpu_info else 0,
            "max_gpu_vram_mb": MAX_GPU_VRAM_MB,
        }
        try:
            await ws.send(json.dumps(heartbeat))
        except Exception:
            break
        await asyncio.sleep(5)



async def run_job(ws, job_id: str, script: str, requirements: str = None, use_gpu: bool = False):
    """Write script to temp file, run inside Docker, stream output."""
    tmpdir = tempfile.mkdtemp()
    script_path = os.path.join(tmpdir, "job.py")

    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)

  
    mount_path = tmpdir.replace("\\", "/")
    if platform.system() == "Windows" and mount_path[1] == ":":
        mount_path = "/" + mount_path[0].lower() + mount_path[2:]

    
    if use_gpu and requirements:
        requirements = filter_requirements(requirements) or None

    if requirements:
        req_path = os.path.join(tmpdir, "requirements.txt")
        with open(req_path, "w", encoding="utf-8") as f:
            f.write(requirements)

    docker_cmd = [
        "docker", "run", "--rm",
        f"--cpus={MAX_CPUS}",
        f"--memory={MAX_RAM_GB}g",
        f"--memory-swap={MAX_RAM_GB}g",
        "--pids-limit=100",
    ]

    if use_gpu and HAS_GPU and MAX_GPU_VRAM_MB > 0:
        docker_cmd.extend([
            "--gpus", "all",
            "--env", "NVIDIA_VISIBLE_DEVICES=all",
            "--env", "NVIDIA_DRIVER_CAPABILITIES=compute,utility",
        ])
        base_image = "compound-gpu:latest"
        print(f"[agent] GPU job — using image {base_image}")
    else:
        base_image = "python:3.11-slim"
        use_gpu = False

    if use_gpu:
        if requirements:
            cmd_str = "pip install -r /app/requirements.txt && python /app/job.py"
            docker_cmd.extend([
                "--network", "bridge",
                "-v", f"{mount_path}:/app",
                base_image,
                "sh", "-c", cmd_str
            ])
        else:
            docker_cmd.extend([
                "--network", "none",
                "-v", f"{mount_path}:/app",
                base_image,
                "python", "/app/job.py"
            ])
    else:
        if requirements:
            docker_cmd.extend([
                "--network", "bridge",
                "-v", f"{mount_path}:/app",
                base_image,
                "sh", "-c", "pip install -r /app/requirements.txt && python /app/job.py"
            ])
        else:
            docker_cmd.extend([
                "--network", "none",
                "-v", f"{mount_path}:/app",
                base_image,
                "python", "/app/job.py"
            ])

    print(f"[agent] running job {job_id}")
    print(f"[agent] cmd: {' '.join(docker_cmd)}")
    print(f"[agent] resource caps: cpus={MAX_CPUS} memory={MAX_RAM_GB}g gpu={'yes' if use_gpu else 'no'} network={'bridge' if requirements else 'none'}")

    if requirements:
        try:
            await ws.send(json.dumps({"type": "log", "job_id": job_id, "line": "[agent] installing requirements..."}))
            await ws.send(json.dumps({"type": "log", "job_id": job_id, "line": "[agent] starting job execution..."}))
        except Exception:
            pass

    if use_gpu:
        try:
            gpu_label = GPU_INFO["name"] if GPU_INFO else "GPU"
            await ws.send(json.dumps({
                "type": "log",
                "job_id": job_id,
                "line": f"[agent] GPU acceleration enabled — {gpu_label} (compound-gpu:latest / cuda:12.1.1-cudnn8)"
            }))
            await ws.send(json.dumps({
                "type": "log",
                "job_id": job_id,
                "line": "[agent] torch + CUDA deps pre-baked in image — only user requirements will be installed"
            }))
        except Exception:
            pass

    start_time = time.time()
    proc = await asyncio.create_subprocess_exec(
        *docker_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        decoded = line.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
        log_msg = {"type": "log", "job_id": job_id, "line": decoded}
        try:
            await ws.send(json.dumps(log_msg))
        except Exception:
            break

    await proc.wait()
    duration = time.time() - start_time
    
    try:
        stats_line = f"[agent] job completed in {duration:.2f}s (Used: {MAX_CPUS} CPUs, {MAX_RAM_GB}GB RAM max)"
        await ws.send(json.dumps({"type": "log", "job_id": job_id, "line": stats_line}))
    except Exception:
        pass

    done_msg = {"type": "done", "job_id": job_id}
    try:
        await ws.send(json.dumps(done_msg))
    except Exception:
        pass

    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass

    print(f"[agent] job {job_id} complete (exit code {proc.returncode})")



async def main():
    print(f"[agent] node_id = {NODE_ID}")
    print(f"[agent] connecting to {COORDINATOR_WS}")

    while True:
        try:
            async with websockets.connect(COORDINATOR_WS) as ws:
                print("[agent] connected to coordinator")

                heartbeat_task = asyncio.create_task(send_heartbeats(ws))

                try:
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "job":
                            job_id = msg["job_id"]
                            script = msg["script"]
                            requirements = msg.get("requirements")
                            use_gpu = msg.get("use_gpu", False)
                            await run_job(ws, job_id, script, requirements, use_gpu)
                except websockets.ConnectionClosed:
                    print("[agent] connection closed")
                finally:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        pass

        except Exception as exc:
            print(f"[agent] connection error: {exc}, retrying in 3s…")

        await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
