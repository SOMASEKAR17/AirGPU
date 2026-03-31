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
import base64
import urllib.request
import urllib.error
import psutil
import websockets

IS_PROD = os.environ.get("IS_PROD", "false").lower() == "true"
COORDINATOR_HTTP = os.environ.get("COORDINATOR_HTTP")
if not COORDINATOR_HTTP or "your-app-name" in COORDINATOR_HTTP:
    if IS_PROD or (os.environ.get("RENDER") and not COORDINATOR_HTTP):
        COORDINATOR_HTTP = "https://airgpu.onrender.com"
    else:
        COORDINATOR_HTTP = "http://localhost:8000"

COORDINATOR_WS = os.environ.get("COORDINATOR_WS")
if not COORDINATOR_WS or "your-app-name" in COORDINATOR_WS:
    if IS_PROD or (os.environ.get("RENDER") and not COORDINATOR_WS):
        COORDINATOR_WS = "wss://airgpu.onrender.com/ws/contributor"
    else:
        COORDINATOR_WS = "ws://localhost:8000/ws/contributor"

NODE_ID = str(uuid.uuid4())

MAX_CPUS = float(os.environ.get("CONTRIB_MAX_CPUS", "2.0"))
MAX_RAM_GB = int(os.environ.get("CONTRIB_MAX_RAM_GB", "4"))
MAX_GPU_VRAM_MB = int(os.environ.get("CONTRIB_MAX_GPU_VRAM_MB", "0"))

PREBAKED_GPU_PACKAGES = {"torch", "torchvision", "torchaudio"}

def filter_requirements(requirements: str) -> str:
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

def upload_checkpoint(job_id: str, epoch: int, filepath: str):
    try:
        with open(filepath, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        payload = json.dumps({
            "epoch": epoch,
            "checkpoint_data": data
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{COORDINATOR_HTTP}/checkpoint/{job_id}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=30)
        print(f"[agent] checkpoint uploaded: epoch {epoch}")
    except Exception as e:
        print(f"[agent] checkpoint upload failed: {e}")

def download_checkpoint(job_id: str) -> tuple:
    try:
        req = urllib.request.Request(
            f"{COORDINATOR_HTTP}/checkpoint/{job_id}",
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if body.get("checkpoint"):
                return body["checkpoint"], body.get("epoch", 0)
    except Exception as e:
        print(f"[agent] checkpoint download failed: {e}")
    return None, 0

def download_dataset(job_id: str, filename: str, dest_dir: str) -> str:
    try:
        url = f"{COORDINATOR_HTTP}/datasets/{job_id}/{filename}"
        req = urllib.request.Request(url, method="GET")
        dest_path = os.path.join(dest_dir, filename)
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(dest_path, "wb") as f:
                f.write(resp.read())
        print(f"[agent] dataset downloaded: {filename}")
        return dest_path
    except Exception as e:
        print(f"[agent] ERROR: dataset download failed: {url} -> {e}")
        return None

def upload_output_file(job_id: str, filepath: str):
    try:
        filename = os.path.basename(filepath)
        with open(filepath, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        payload = json.dumps({
            "filename": filename,
            "data": data
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{COORDINATOR_HTTP}/upload-output/{job_id}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=60)
        print(f"[agent] output uploaded: {filename}")
    except Exception as e:
        print(f"[agent] output upload failed: {filename} — {e}")

def collect_and_upload_outputs(job_id: str, tmpdir: str, output_extensions: list):
    default_extensions = [".pkl", ".pt", ".h5", ".csv", ".json", ".txt", ".png", ".jpg", ".npy", ".npz", ".onnx", ".pth"]
    extensions = set(output_extensions or default_extensions)
    for fname in os.listdir(tmpdir):
        fpath = os.path.join(tmpdir, fname)
        if not os.path.isfile(fpath):
            continue
        if fname == "job.py" or fname == "requirements.txt" or fname == "checkpoint.pt":
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext in extensions:
            upload_output_file(job_id, fpath)

async def send_heartbeats(ws):
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

async def run_job(ws, job_id: str, script: str, requirements: str = None, use_gpu: bool = False, resume_from_epoch: int = 0, coordinator_url: str = None, dataset_filename: str = None, output_extensions: list = None):
    global COORDINATOR_HTTP
    if coordinator_url:
        COORDINATOR_HTTP = coordinator_url

    tmpdir = tempfile.mkdtemp()
    script_path = os.path.join(tmpdir, "job.py")
    checkpoint_path = os.path.join(tmpdir, "checkpoint.pt")

    if resume_from_epoch > 0:
        print(f"[agent] resuming job {job_id} from epoch {resume_from_epoch}")
        checkpoint_data, saved_epoch = download_checkpoint(job_id)
        if checkpoint_data:
            with open(checkpoint_path, "wb") as f:
                f.write(base64.b64decode(checkpoint_data))
            print(f"[agent] checkpoint restored: epoch {saved_epoch}")
            try:
                await ws.send(json.dumps({
                    "type": "log",
                    "job_id": job_id,
                    "line": f"[agent] resuming from checkpoint at epoch {saved_epoch}"
                }))
            except Exception:
                pass
        else:
            print(f"[agent] no checkpoint found — starting from epoch 0")

    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)

    if dataset_filename:
        dataset_dest = download_dataset(job_id, dataset_filename, tmpdir)
        if dataset_dest:
            try:
                await ws.send(json.dumps({
                    "type": "log",
                    "job_id": job_id,
                    "line": f"[agent] dataset loaded: {dataset_filename}"
                }))
            except Exception:
                pass
        else:
            try:
                await ws.send(json.dumps({
                    "type": "log",
                    "job_id": job_id,
                    "line": f"[agent] warning: dataset {dataset_filename} could not be loaded"
                }))
            except Exception:
                pass

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
        "-w", "/app",
        f"--cpus={MAX_CPUS}",
        f"--memory={MAX_RAM_GB}g",
        f"--memory-swap={MAX_RAM_GB}g",
        "--pids-limit=100",
        "-e", f"JOB_ID={job_id}",
        "-e", f"RESUME_FROM_EPOCH={resume_from_epoch}",
        "-e", f"COORDINATOR_HTTP={COORDINATOR_HTTP}",
        "-e", f"CHECKPOINT_PATH=/app/checkpoint.pt"
    ]

    if dataset_filename:
        docker_cmd.extend(["-e", f"DATASET_PATH=/app/{dataset_filename}"])

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

    cmd_diagnostic = "ls -R /app && "
    if requirements:
        cmd_str = f"{cmd_diagnostic}pip install -r /app/requirements.txt && python /app/job.py"
        docker_cmd.extend([
            "--network", "bridge",
            "-v", f"{mount_path}:/app",
            base_image,
            "sh", "-c", cmd_str
        ])
    else:
        cmd_str = f"{cmd_diagnostic}python /app/job.py"
        docker_cmd.extend([
            "--network", "bridge",
            "-v", f"{mount_path}:/app",
            base_image,
            "sh", "-c", cmd_str
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

        if decoded.startswith("CHECKPOINT:"):
            parts = decoded.split(":")
            if len(parts) >= 3:
                try:
                    epoch = int(parts[1])
                    ckpt_file = parts[2].strip()
                    upload_checkpoint(job_id, epoch, os.path.join(tmpdir, os.path.basename(ckpt_file)))
                except Exception as e:
                    print(f"[agent] checkpoint parse error: {e}")
            continue

        log_msg = {"type": "log", "job_id": job_id, "line": decoded}
        try:
            await ws.send(json.dumps(log_msg))
        except Exception:
            break

    await proc.wait()
    duration = time.time() - start_time
    
    if proc.returncode == 0:
        collect_and_upload_outputs(job_id, tmpdir, output_extensions or [])
        try:
            await ws.send(json.dumps({
                "type": "log",
                "job_id": job_id,
                "line": "[agent] output files uploaded — available for download"
            }))
        except Exception:
            pass

    try:
        stats_line = f"[agent] job completed in {duration:.2f}s (Used: {MAX_CPUS} CPUs, {MAX_RAM_GB}GB RAM max)"
        await ws.send(json.dumps({"type": "log", "job_id": job_id, "line": stats_line}))
    except Exception:
        pass

    gpu_vram_gb = 0.0
    if use_gpu and GPU_INFO:
        gpu_vram_gb = round(MAX_GPU_VRAM_MB / 1024, 2)

    done_msg = {
        "type": "done",
        "job_id": job_id,
        "gpu_vram_gb": gpu_vram_gb
    }
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

    AUTH_TOKEN = os.environ.get("CONTRIB_AUTH_TOKEN", "")
    ws_url = COORDINATOR_WS
    if AUTH_TOKEN:
        ws_url = f"{COORDINATOR_WS}?token={AUTH_TOKEN}"
    
    while True:
        try:
            async with websockets.connect(ws_url) as ws:
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
                            resume_from_epoch = msg.get("resume_from_epoch", 0)
                            coordinator_url = msg.get("coordinator_url")
                            dataset_filename = msg.get("dataset_filename")
                            output_extensions = msg.get("output_extensions", [])
                            await run_job(ws, job_id, script, requirements, use_gpu, resume_from_epoch, coordinator_url, dataset_filename, output_extensions)
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
