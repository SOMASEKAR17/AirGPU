import os
import base64
import uuid
import json
from typing import Dict, Optional
from collections import deque
import time
import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

import firebase_admin
from firebase_admin import credentials, auth as firebase_auth, db as firebase_db

from scanner import scan_code, format_scan_result

COORDINATOR_HTTP = os.environ.get("COORDINATOR_HTTP", "http://localhost:8000")

SERVICE_ACCOUNT_PATH = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "serviceAccount.json")

if os.path.exists(SERVICE_ACCOUNT_PATH):
    cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
    firebase_admin.initialize_app(cred, {
        "databaseURL": "https://airgpu-928f3-default-rtdb.asia-southeast1.firebasedatabase.app"
    })
    AUTH_ENABLED = True
    print("[coordinator] Firebase auth enabled")
else:
    firebase_admin.initialize_app()
    AUTH_ENABLED = False
    print("[coordinator] Firebase auth disabled — serviceAccount.json not found")

async def optional_verify_token(credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False))):
    if not AUTH_ENABLED:
        return {"uid": "anonymous", "email": "anonymous"}
    if not credentials:
        raise HTTPException(status_code=401, detail="Authorization required")
    try:
        decoded = firebase_auth.verify_id_token(credentials.credentials)
        return decoded
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

CHECKPOINT_DIR = os.path.join(os.path.dirname(__file__), "checkpoints")
os.makedirs(CHECKPOINT_DIR, exist_ok=True)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ContributorConnection:
    def __init__(self, ws: WebSocket, node_id: Optional[str] = None):
        self.ws = ws
        self.node_id = node_id
        self.busy = False
        self.current_job: Optional[str] = None
        self.cpu_free: float = 0.0
        self.ram_free: float = 0.0
        self.has_gpu: bool = False
        self.gpu_name: str = None
        self.gpu_vram_free_mb: int = 0
        self.gpu_vram_total_mb: int = 0
        self.gpu_utilization: int = 0
        self.max_gpu_vram_mb: int = 0
        self.max_cpus: float = 2.0
        self.max_ram_gb: int = 4

class Job:
    def __init__(self, job_id: str, script: str, requirements: str = None, use_gpu: bool = False):
        self.job_id = job_id
        self.script = script
        self.requirements = requirements
        self.use_gpu = use_gpu
        self.submitter_uid: str = ""
        self.submitter_email: str = ""
        self.done = False
        self.status = "pending"
        self.contributor_node_id: Optional[str] = None
        self.submitted_at = time.time()
        self.checkpoint_epoch = 0
        self.checkpoint_path = None
        self.retry_count = 0
        self.max_retries = 3

contributors: Dict[int, ContributorConnection] = {}
jobs: Dict[str, Job] = {}
submitter_connections: Dict[str, WebSocket] = {}
pending_jobs: deque = deque()

async def db_upsert_user(uid: str, email: str, display_name: str):
    def _write():
        ref = firebase_db.reference(f"/users/{uid}")
        existing = ref.get()
        if existing:
            ref.update({
                "last_seen": time.time(),
                "email": email,
                "displayName": display_name,
            })
        else:
            ref.set({
                "uid": uid,
                "email": email,
                "displayName": display_name,
                "first_seen": time.time(),
                "last_seen": time.time(),
                "total_jobs_submitted": 0,
                "total_jobs_contributed": 0,
            })
    try:
        await asyncio.to_thread(_write)
    except Exception as e:
        print(f"[db] upsert_user failed: {e}")

async def db_create_job(job: "Job", submitter_uid: str, submitter_email: str):
    def _write():
        firebase_db.reference(f"/jobs/{job.job_id}").set({
            "job_id": job.job_id,
            "submitter_uid": submitter_uid,
            "submitter_email": submitter_email,
            "contributor_node_id": None,
            "contributor_uid": None,
            "status": "pending",
            "use_gpu": job.use_gpu,
            "gpu_name": None,
            "script_lines": len(job.script.splitlines()),
            "requirements_lines": len(job.requirements.splitlines()) if job.requirements else 0,
            "submitted_at": job.submitted_at,
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
            "cpu_cores": None,
            "ram_gb": None,
            "cpu_time_seconds": None,
            "gpu_time_seconds": None,
            "retry_count": 0,
            "checkpoint_epoch": 0,
        })
        firebase_db.reference(f"/users/{submitter_uid}/total_jobs_submitted").transaction(
            lambda current: (current or 0) + 1
        )
    try:
        await asyncio.to_thread(_write)
    except Exception as e:
        print(f"[db] create_job failed: {e}")

async def db_job_started(job_id: str, contributor_node_id: str, contributor_uid: str, cpu_cores: float, ram_gb: int, gpu_name: str):
    def _write():
        firebase_db.reference(f"/jobs/{job_id}").update({
            "status": "running",
            "contributor_node_id": contributor_node_id,
            "contributor_uid": contributor_uid,
            "started_at": time.time(),
            "cpu_cores": cpu_cores,
            "ram_gb": ram_gb,
            "gpu_name": gpu_name,
        })
    try:
        await asyncio.to_thread(_write)
    except Exception as e:
        print(f"[db] job_started failed: {e}")

async def db_job_completed(job_id: str, node_id: str, contributor_uid: str, duration: float, cpu_cores: float, ram_gb: int, use_gpu: bool, gpu_name: str):
    cpu_time = round(cpu_cores * duration, 1)
    gpu_time = round(duration, 1) if use_gpu else 0

    def _write():
        firebase_db.reference(f"/jobs/{job_id}").update({
            "status": "complete",
            "contributor_node_id": node_id,
            "completed_at": time.time(),
            "duration_seconds": duration,
            "cpu_time_seconds": cpu_time,
            "gpu_time_seconds": gpu_time,
        })

        contrib_ref = firebase_db.reference(f"/contributors/{node_id}")
        existing = contrib_ref.get()
        if existing:
            contrib_ref.update({
                "total_jobs_executed": (existing.get("total_jobs_executed") or 0) + 1,
                "total_cpu_time_seconds": round((existing.get("total_cpu_time_seconds") or 0) + cpu_time, 1),
                "total_gpu_time_seconds": round((existing.get("total_gpu_time_seconds") or 0) + gpu_time, 1),
                "last_seen": time.time(),
            })

        if contributor_uid:
            firebase_db.reference(f"/users/{contributor_uid}/total_jobs_contributed").transaction(
                lambda current: (current or 0) + 1
            )

    try:
        await asyncio.to_thread(_write)
    except Exception as e:
        print(f"[db] job_completed failed: {e}")

async def db_upsert_contributor(node_id: str, uid: str, email: str, max_cpus: float, max_ram_gb: int, max_gpu_vram_mb: int, gpu_name: str):
    def _write():
        ref = firebase_db.reference(f"/contributors/{node_id}")
        existing = ref.get()
        if existing:
            ref.update({
                "last_seen": time.time(),
                "max_cpus": max_cpus,
                "max_ram_gb": max_ram_gb,
                "max_gpu_vram_mb": max_gpu_vram_mb,
                "gpu_name": gpu_name,
            })
        else:
            ref.set({
                "node_id": node_id,
                "uid": uid,
                "email": email,
                "max_cpus": max_cpus,
                "max_ram_gb": max_ram_gb,
                "max_gpu_vram_mb": max_gpu_vram_mb,
                "gpu_name": gpu_name,
                "total_jobs_executed": 0,
                "total_cpu_time_seconds": 0,
                "total_gpu_time_seconds": 0,
                "first_seen": time.time(),
                "last_seen": time.time(),
            })
    try:
        await asyncio.to_thread(_write)
    except Exception as e:
        print(f"[db] upsert_contributor failed: {e}")

def score_contributor(conn, use_gpu: bool) -> float:
    if use_gpu:
        return (
            conn.cpu_free * 0.3 +
            conn.ram_free * 0.3 +
            (conn.gpu_vram_free_mb / 1000) * 0.4
        )
    return conn.cpu_free * 0.5 + conn.ram_free * 0.5

class SubmitJobRequest(BaseModel):
    script: str
    requirements: Optional[str] = None
    use_gpu: bool = False
    submitter_email: Optional[str] = ""

@app.post("/submit-job")
async def submit_job(req: SubmitJobRequest, user=Depends(optional_verify_token)):
    print(f"[coordinator] job submitted by {user.get('email', 'unknown')}")
    try:
        scan_result = scan_code(req.script)
        if not scan_result.passed:
            return {
                "job_id": None,
                "assigned": False,
                "rejected": True,
                "scan_violations": [
                    f"Line {v['line']}: {v['message']}"
                    for v in scan_result.violations
                ],
                "message": format_scan_result(scan_result)
            }
    except Exception as exc:
        print(f"[coordinator] scanner error: {exc}")

    job_id = str(uuid.uuid4())
    job = Job(job_id=job_id, script=req.script, requirements=req.requirements, use_gpu=req.use_gpu)
    submitter_uid = user.get("uid", "anonymous") if user else "anonymous"
    submitter_email = user.get("email", "anonymous") if user else "anonymous"
    job.submitter_uid = submitter_uid
    job.submitter_email = submitter_email
    jobs[job_id] = job
    asyncio.create_task(db_upsert_user(submitter_uid, submitter_email, user.get("name", "") if user else ""))
    asyncio.create_task(db_create_job(job, submitter_uid, submitter_email))

    assigned = False
    available = [
        (cid, c) for cid, c in contributors.items()
        if not c.busy and (not job.use_gpu or c.has_gpu)
    ]
    if available:
        best_cid, best_conn = max(
            available,
            key=lambda x: score_contributor(x[1], job.use_gpu)
        )
        best_conn.busy = True
        best_conn.current_job = job_id
        job.contributor_node_id = best_conn.node_id
        job.status = "running"
        try:
            await best_conn.ws.send_json({
                "type": "job",
                "job_id": job_id,
                "script": job.script,
                "requirements": job.requirements,
                "use_gpu": job.use_gpu,
                "resume_from_epoch": job.checkpoint_epoch,
                "coordinator_url": COORDINATOR_HTTP,
            })
            assigned = True
            
            if best_conn.node_id:
                asyncio.create_task(db_job_started(
                    job_id,
                    best_conn.node_id,
                    "",
                    best_conn.max_cpus,
                    best_conn.max_ram_gb,
                    best_conn.gpu_name or "",
                ))
            
            sub_ws = submitter_connections.get(job_id)
            if sub_ws:
                try:
                    await sub_ws.send_json({
                        "type": "log",
                        "job_id": job_id,
                        "line": "[coordinator] tip: print CHECKPOINT:<epoch>:<filename> in your script to save checkpoints"
                    })
                except Exception:
                    pass
        except Exception:
            best_conn.busy = False
            best_conn.current_job = None
            job.status = "pending"

    if not assigned:
        pending_jobs.append(job_id)

    return {"job_id": job_id, "assigned": assigned}

async def try_assign_pending():
    while pending_jobs:
        job_id = pending_jobs[0]
        job = jobs[job_id]
        
        available = [
            (cid, c) for cid, c in contributors.items()
            if not c.busy and (not job.use_gpu or c.has_gpu)
        ]
        if not available:
            break  
        
        best_cid, best_conn = max(
            available,
            key=lambda x: score_contributor(x[1], job.use_gpu)
        )
        
        pending_jobs.popleft()
        
        best_conn.busy = True
        best_conn.current_job = job_id
        job.contributor_node_id = best_conn.node_id
        job.status = "running"
        
        try:
            await best_conn.ws.send_json({
                "type": "job",
                "job_id": job_id,
                "script": job.script,
                "requirements": job.requirements,
                "use_gpu": job.use_gpu,
                "resume_from_epoch": job.checkpoint_epoch,
                "coordinator_url": COORDINATOR_HTTP,
            })
            
            if best_conn.node_id:
                asyncio.create_task(db_job_started(
                    job_id,
                    best_conn.node_id,
                    "",
                    best_conn.max_cpus,
                    best_conn.max_ram_gb,
                    best_conn.gpu_name or "",
                ))

            sub_ws = submitter_connections.get(job_id)
            if sub_ws:
                try:
                    await sub_ws.send_json({
                        "type": "log",
                        "job_id": job_id,
                        "line": "[coordinator] tip: print CHECKPOINT:<epoch>:<filename> in your script to save checkpoints"
                    })
                    await sub_ws.send_json({
                        "type": "status",
                        "job_id": job_id,
                        "status": "running",
                        "queue_position": 0
                    })
                except Exception:
                    pass

        except Exception:
            best_conn.busy = False
            best_conn.current_job = None
            job.status = "pending"
            pending_jobs.appendleft(job_id)
            break

@app.post("/checkpoint/{job_id}")
async def receive_checkpoint(job_id: str, request: Request):
    body = await request.json()
    epoch = body.get("epoch", 0)
    checkpoint_data = body.get("checkpoint_data")

    if job_id not in jobs:
        return {"error": "job not found"}

    job = jobs[job_id]
    checkpoint_filename = f"{job_id}_epoch_{epoch}.pt.b64"
    checkpoint_path = os.path.join(CHECKPOINT_DIR, checkpoint_filename)

    with open(checkpoint_path, "w") as f:
        f.write(checkpoint_data)

    if job.checkpoint_path and os.path.exists(job.checkpoint_path):
        try:
            os.remove(job.checkpoint_path)
        except Exception:
            pass

    job.checkpoint_epoch = epoch
    job.checkpoint_path = checkpoint_path
    print(f"[coordinator] checkpoint saved for job {job_id} epoch {epoch}")

    sub_ws = submitter_connections.get(job_id)
    if sub_ws:
        try:
            await sub_ws.send_json({
                "type": "checkpoint",
                "job_id": job_id,
                "epoch": epoch
            })
        except Exception:
            pass

    return {"saved": True, "epoch": epoch}

@app.get("/checkpoint/{job_id}")
async def get_checkpoint(job_id: str):
    if job_id not in jobs:
        return {"checkpoint": None}

    job = jobs[job_id]
    if not job.checkpoint_path or not os.path.exists(job.checkpoint_path):
        return {"checkpoint": None, "epoch": 0}

    with open(job.checkpoint_path, "r") as f:
        checkpoint_data = f.read()

    return {
        "checkpoint": checkpoint_data,
        "epoch": job.checkpoint_epoch
    }

@app.websocket("/ws/contributor")
async def ws_contributor(ws: WebSocket):
    await ws.accept()
    token = ws.query_params.get("token")
    if AUTH_ENABLED and token:
        try:
            firebase_auth.verify_id_token(token)
        except Exception:
            await ws.close(code=1008)
            return
            
    cid = id(ws)
    conn = ContributorConnection(ws=ws)
    contributors[cid] = conn
    print(f"[coordinator] contributor connected  (cid={cid})")

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "heartbeat":
                is_first_heartbeat = conn.node_id is None
                conn.node_id = msg.get("node_id")
                conn.cpu_free = msg.get("cpu_free", 0)
                conn.ram_free = msg.get("ram_free", 0)
                conn.has_gpu = msg.get("has_gpu", False)
                conn.gpu_name = msg.get("gpu_name")
                conn.gpu_vram_free_mb = msg.get("gpu_vram_free_mb", 0)
                conn.gpu_vram_total_mb = msg.get("gpu_vram_total_mb", 0)
                conn.gpu_utilization = msg.get("gpu_utilization", 0)
                conn.max_gpu_vram_mb = msg.get("max_gpu_vram_mb", 0)
                conn.max_cpus = msg.get("max_cpus", 2.0)
                conn.max_ram_gb = msg.get("max_ram_gb", 4)

                if is_first_heartbeat and conn.node_id:
                    token = ws.query_params.get("token")
                    uid = "anonymous"
                    email = "anonymous"
                    if AUTH_ENABLED and token:
                        try:
                            decoded = firebase_auth.verify_id_token(token)
                            uid = decoded.get("uid", "anonymous")
                            email = decoded.get("email", "anonymous")
                        except Exception:
                            pass
                    asyncio.create_task(db_upsert_contributor(
                        conn.node_id, uid, email,
                        conn.max_cpus, conn.max_ram_gb, conn.max_gpu_vram_mb, conn.gpu_name or ""
                    ))

                await try_assign_pending()

            elif msg_type == "log":
                job_id = msg.get("job_id")
                line = msg.get("line", "")
                sub_ws = submitter_connections.get(job_id)
                if sub_ws:
                    try:
                        await sub_ws.send_json({
                            "type": "log",
                            "job_id": job_id,
                            "line": line,
                        })
                    except Exception:
                        pass

            elif msg_type == "done":
                job_id = msg.get("job_id")
                if job_id in jobs:
                    jobs[job_id].done = True
                    jobs[job_id].status = "complete"
                    
                    job = jobs[job_id]
                    duration = round(time.time() - job.submitted_at, 1)
                    asyncio.create_task(db_job_completed(
                        job_id,
                        conn.node_id,
                        "",
                        duration,
                        conn.max_cpus,
                        conn.max_ram_gb,
                        job.use_gpu,
                        conn.gpu_name or "",
                    ))

                conn.busy = False
                conn.current_job = None
                sub_ws = submitter_connections.get(job_id)
                if sub_ws:
                    try:
                        await sub_ws.send_json({
                            "type": "done",
                            "job_id": job_id,
                        })
                    except Exception:
                        pass
                await try_assign_pending()

    except WebSocketDisconnect:
        print(f"[coordinator] contributor disconnected (cid={cid})")
    except Exception as exc:    
        print(f"[coordinator] contributor error: {exc}")
    finally:
        if conn.current_job:
            job = jobs.get(conn.current_job)
            if job and not job.done:
                job.retry_count += 1
                if job.retry_count <= job.max_retries:
                    job.status = "pending"
                    job.contributor_node_id = None
                    pending_jobs.appendleft(conn.current_job)
                    print(f"[coordinator] contributor dropped — requeued job {conn.current_job} (retry {job.retry_count}/{job.max_retries})")
                    sub_ws = submitter_connections.get(conn.current_job)
                    if sub_ws:
                        try:
                            await sub_ws.send_json({
                                "type": "status",
                                "job_id": conn.current_job,
                                "status": "pending",
                                "queue_position": 1,
                                "message": f"Contributor disconnected — reassigning job (attempt {job.retry_count}/{job.max_retries})...",
                                "checkpoint_epoch": job.checkpoint_epoch
                            })
                        except Exception:
                            pass
                else:
                    job.status = "failed"
                    print(f"[coordinator] job {conn.current_job} failed after {job.max_retries} retries")
                    sub_ws = submitter_connections.get(conn.current_job)
                    if sub_ws:
                        try:
                            await sub_ws.send_json({
                                "type": "failed",
                                "job_id": conn.current_job,
                                "message": f"Job failed after {job.max_retries} attempts — no contributors available"
                            })
                        except Exception:
                            pass
        contributors.pop(cid, None)
        await try_assign_pending()

def get_queue_position(job_id: str) -> int:
    queue_list = list(pending_jobs)
    if job_id in queue_list:
        return queue_list.index(job_id) + 1
    return 0

@app.websocket("/ws/submitter/{job_id}")
async def ws_submitter(ws: WebSocket, job_id: str):
    await ws.accept()
    token = ws.query_params.get("token")
    if AUTH_ENABLED and token:
        try:
            firebase_auth.verify_id_token(token)
        except Exception:
            await ws.close(code=1008)
            return
            
    submitter_connections[job_id] = ws
    print(f"[coordinator] submitter connected for job {job_id}")

    job = jobs.get(job_id)
    if job:
        await ws.send_json({
            "type": "status",
            "job_id": job_id,
            "status": job.status,
            "queue_position": get_queue_position(job_id)
        })

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        print(f"[coordinator] submitter disconnected for job {job_id}")
    except Exception as exc:
        print(f"[coordinator] submitter error: {exc}")
    finally:
        submitter_connections.pop(job_id, None)

@app.get("/queue-status")
async def queue_status():
    return {
        "pending_jobs": list(pending_jobs),
        "total_jobs": len(jobs),
        "jobs": [
            {
                "job_id": j.job_id,
                "status": j.status,
                "retry_count": j.retry_count,
                "checkpoint_epoch": j.checkpoint_epoch,
            }
            for j in jobs.values()
        ],
        "contributors": [
            {
                "node_id": c.node_id,
                "busy": c.busy,
                "current_job": c.current_job,
                "cpu_free": c.cpu_free,
                "ram_free": c.ram_free,
                "has_gpu": c.has_gpu,
                "gpu_name": c.gpu_name,
                "gpu_vram_free_mb": c.gpu_vram_free_mb,
                "gpu_utilization": c.gpu_utilization,
            }
            for c in contributors.values()
        ]
    }

@app.get("/db/contributor/{node_id}")
async def get_contributor_stats(node_id: str):
    def _read():
        return firebase_db.reference(f"/contributors/{node_id}").get()
    try:
        data = await asyncio.to_thread(_read)
        return data or {}
    except Exception as e:
        return {"error": str(e)}


@app.get("/db/contributor/{node_id}/jobs")
async def get_contributor_jobs(node_id: str):
    def _read():
        all_jobs = firebase_db.reference("/jobs").get()
        if not all_jobs:
            return []
        matched = [
            v for v in all_jobs.values()
            if v.get("contributor_node_id") == node_id
        ]
        matched.sort(key=lambda x: x.get("completed_at") or 0, reverse=True)
        return matched[:50]
    try:
        data = await asyncio.to_thread(_read)
        return {"jobs": data}
    except Exception as e:
        return {"jobs": [], "error": str(e)}


@app.get("/db/user/{uid}")
async def get_user_stats(uid: str):
    def _read():
        return firebase_db.reference(f"/users/{uid}").get()
    try:
        data = await asyncio.to_thread(_read)
        return data or {}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
