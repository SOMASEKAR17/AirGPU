import os
import base64
import uuid
import json
from typing import Dict, Optional, List
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

COORDINATOR_HTTP = os.environ.get("COORDINATOR_HTTP")
if not COORDINATOR_HTTP or "your-app-name" in COORDINATOR_HTTP:
    if os.environ.get("RENDER"):
        COORDINATOR_HTTP = "https://airgpu.onrender.com"
    else:
        COORDINATOR_HTTP = "http://localhost:8000"

import json

SERVICE_ACCOUNT_PATH = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "serviceAccount.json")
SERVICE_ACCOUNT_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")

if SERVICE_ACCOUNT_JSON:
    try:
        service_account_info = json.loads(SERVICE_ACCOUNT_JSON)
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred, {
            "databaseURL": "https://airgpu-928f3-default-rtdb.asia-southeast1.firebasedatabase.app"
        })
        AUTH_ENABLED = True
        print("[coordinator] Firebase auth enabled via environment variable")
    except Exception as e:
        print(f"[coordinator] Failed to initialize Firebase from environment variable: {e}")
        AUTH_ENABLED = False
elif os.path.exists(SERVICE_ACCOUNT_PATH):
    cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
    firebase_admin.initialize_app(cred, {
        "databaseURL": "https://airgpu-928f3-default-rtdb.asia-southeast1.firebasedatabase.app"
    })
    AUTH_ENABLED = True
    print("[coordinator] Firebase auth enabled via serviceAccount.json")
else:
    firebase_admin.initialize_app()
    AUTH_ENABLED = False
    print("[coordinator] Firebase auth disabled — serviceAccount.json or environment variable not found")

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
DATASETS_DIR = os.path.join(os.path.dirname(__file__), "datasets")
OUTPUTS_DIR = os.path.join(os.path.dirname(__file__), "outputs")
os.makedirs(CHECKPOINT_DIR, exist_ok=True)
os.makedirs(DATASETS_DIR, exist_ok=True)
os.makedirs(OUTPUTS_DIR, exist_ok=True)

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
        self.current_job = None
        self.cpu_free = 0.0
        self.ram_free = 0.0
        self.has_gpu = False
        self.gpu_name = None
        self.gpu_vram_free_mb = 0
        self.gpu_vram_total_mb = 0
        self.gpu_utilization = 0
        self.max_gpu_vram_mb = 0
        self.max_cpus = 2.0
        self.max_ram_gb = 4
        self.uid = "anonymous"
        self.gpu_vram_gb = 0.0

class Job:
    def __init__(self, job_id: str, script: str, requirements: str = None, use_gpu: bool = False, dataset_filename: Optional[str] = None):
        self.job_id = job_id
        self.script = script
        self.requirements = requirements
        self.use_gpu = use_gpu
        self.dataset_filename = dataset_filename
        self.dataset_ready = (dataset_filename is None)
        self.submitter_uid = ""
        self.submitter_email = ""
        self.done = False
        self.status = "pending"
        self.contributor_node_id = None
        self.submitted_at = time.time()
        self.checkpoint_epoch = 0
        self.checkpoint_path = None
        self.retry_count = 0
        self.max_retries = 3
        self.estimated_cost = 1.0
        self.contributions = []
        self.current_contributor_start = None
        self.dataset_filename = None
        self.output_extensions = []

contributors: Dict[int, ContributorConnection] = {}
jobs: Dict[str, Job] = {}
submitter_connections: Dict[str, WebSocket] = {}
pending_jobs: deque = deque()

async def db_initialize_user_credits(uid: str):
    def _write():
        ref = firebase_db.reference(f"/credits/{uid}")
        existing = ref.get()
        if not existing:
            ref.set({
                "uid": uid,
                "balance": 100.0,
                "total_earned": 0.0,
                "total_spent": 0.0,
                "initialized_at": time.time()
            })
    try:
        await asyncio.to_thread(_write)
    except Exception as e:
        print(f"[db] initialize_credits failed: {e}")

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
            return False
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
            return True
    try:
        is_new = await asyncio.to_thread(_write)
        if is_new:
            await db_initialize_user_credits(uid)
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
            "dataset_filename": job.dataset_filename,
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

def calculate_job_cost(duration_seconds: float, cpu_cores: float, ram_gb: int, use_gpu: bool, gpu_vram_gb: float) -> float:
    cpu_cost = cpu_cores * duration_seconds * 0.01
    ram_cost = ram_gb * duration_seconds * 0.005
    gpu_cost = gpu_vram_gb * duration_seconds * 0.05 if use_gpu else 0
    total = round(cpu_cost + ram_cost + gpu_cost, 2)
    return max(total, 1.0)

async def db_job_completed(job_id: str, node_id: str, contributor_uid: str, duration: float, cpu_cores: float, ram_gb: int, use_gpu: bool, gpu_name: str, gpu_vram_gb: float, submitter_uid: str, estimated_cost: float):
    actual_cost = calculate_job_cost(duration, cpu_cores, ram_gb, use_gpu, gpu_vram_gb)
    contributor_earn = round(actual_cost * 0.8, 2)

    def _write():
        firebase_db.reference(f"/jobs/{job_id}").update({
            "status": "complete",
            "contributor_node_id": node_id,
            "completed_at": time.time(),
            "duration_seconds": duration,
            "cpu_time_seconds": round(cpu_cores * duration, 1),
            "gpu_time_seconds": round(duration, 1) if use_gpu else 0,
            "actual_cost_credits": actual_cost,
            "contributor_earned_credits": contributor_earn,
        })

        contrib_ref = firebase_db.reference(f"/contributors/{node_id}")
        existing = contrib_ref.get()
        if existing:
            contrib_ref.update({
                "total_jobs_executed": (existing.get("total_jobs_executed") or 0) + 1,
                "total_cpu_time_seconds": round((existing.get("total_cpu_time_seconds") or 0) + cpu_cores * duration, 1),
                "total_gpu_time_seconds": round((existing.get("total_gpu_time_seconds") or 0) + (duration if use_gpu else 0), 1),
                "total_credits_earned": round((existing.get("total_credits_earned") or 0) + contributor_earn, 2),
                "last_seen": time.time(),
            })

        if contributor_uid and contributor_uid != "anonymous":
            firebase_db.reference(f"/users/{contributor_uid}/total_jobs_contributed").transaction(
                lambda current: (current or 0) + 1
            )

        if submitter_uid and submitter_uid != "anonymous":
            firebase_db.reference(f"/users/{submitter_uid}/total_jobs_submitted").transaction(
                lambda current: (current or 0) + 0
            )

    try:
        await asyncio.to_thread(_write)

        if AUTH_ENABLED:
            if submitter_uid and submitter_uid != "anonymous":
                if estimated_cost > actual_cost:
                    refund = round(estimated_cost - actual_cost, 2)
                    await db_refund_credits(submitter_uid, refund, job_id)

            if contributor_uid and contributor_uid != "anonymous":
                await db_earn_credits(contributor_uid, contributor_earn, job_id)

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
        if uid and uid != "anonymous":
            await db_initialize_user_credits(uid)
    except Exception as e:
        print(f"[db] upsert_contributor failed: {e}")

async def db_get_credit_balance(uid: str) -> float:
    def _read():
        ref = firebase_db.reference(f"/credits/{uid}/balance")
        val = ref.get()
        return float(val) if val is not None else 100.0
    try:
        return await asyncio.to_thread(_read)
    except Exception:
        return 100.0

async def db_deduct_credits(uid: str, amount: float, job_id: str) -> bool:
    def _write():
        ref = firebase_db.reference(f"/credits/{uid}")
        data = ref.get()
        if not data:
            ref.set({
                "uid": uid,
                "balance": 100.0,
                "total_earned": 0.0,
                "total_spent": 0.0,
                "initialized_at": time.time()
            })
            data = {"balance": 100.0, "total_spent": 0.0}
        balance = float(data.get("balance", 0))
        if balance < amount:
            return False
        new_balance = round(balance - amount, 2)
        ref.update({
            "balance": new_balance,
            "total_spent": round(float(data.get("total_spent", 0)) + amount, 2)
        })
        firebase_db.reference(f"/credit_transactions/{uid}").push({
            "type": "spent",
            "amount": -amount,
            "job_id": job_id,
            "balance_after": new_balance,
            "timestamp": time.time()
        })
        return True
    try:
        return await asyncio.to_thread(_write)
    except Exception as e:
        print(f"[db] deduct_credits failed: {e}")
        return False

async def db_earn_credits(uid: str, amount: float, job_id: str):
    def _write():
        ref = firebase_db.reference(f"/credits/{uid}")
        data = ref.get()
        if not data:
            ref.set({
                "uid": uid,
                "balance": 100.0,
                "total_earned": 0.0,
                "total_spent": 0.0,
                "initialized_at": time.time()
            })
            data = {"balance": 100.0, "total_earned": 0.0, "total_spent": 0.0}
            
        balance = float(data.get("balance", 0))
        new_balance = round(balance + amount, 2)
        ref.update({
            "balance": new_balance,
            "total_earned": round(float(data.get("total_earned", 0)) + amount, 2)
        })
        balance_after = new_balance
        firebase_db.reference(f"/credit_transactions/{uid}").push({
            "type": "earned",
            "amount": amount,
            "job_id": job_id,
            "balance_after": balance_after,
            "timestamp": time.time()
        })
    try:
        await asyncio.to_thread(_write)
    except Exception as e:
        print(f"[db] earn_credits failed: {e}")

async def db_refund_credits(uid: str, amount: float, job_id: str):
    def _write():
        ref = firebase_db.reference(f"/credits/{uid}")
        data = ref.get()
        if not data:
            ref.set({
                "uid": uid,
                "balance": 100.0,
                "total_earned": 0.0,
                "total_spent": 0.0,
                "initialized_at": time.time()
            })
            data = {"balance": 100.0, "total_spent": 0.0}
        balance = float(data.get("balance", 0))
        new_balance = round(balance + amount, 2)
        ref.update({
            "balance": new_balance,
            "total_spent": round(max(float(data.get("total_spent", 0)) - amount, 0), 2)
        })
        firebase_db.reference(f"/credit_transactions/{uid}").push({
            "type": "refund",
            "amount": amount,
            "job_id": job_id,
            "balance_after": new_balance,
            "timestamp": time.time()
        })
    try:
        await asyncio.to_thread(_write)
    except Exception as e:
        print(f"[db] refund_credits failed: {e}")

async def db_pay_partial_contributors(job_id: str, contributions: list, total_cost: float):
    if not contributions:
        return
    total_duration = sum(c["duration_seconds"] for c in contributions)
    if total_duration <= 0:
        return
    pool = round(total_cost, 2)
    for partial in contributions:
        fraction = partial["duration_seconds"] / total_duration
        earn = round(pool * fraction, 2)
        uid = partial.get("uid", "anonymous")
        node_id = partial.get("node_id")
        if earn > 0 and uid and uid != "anonymous":
            await db_earn_credits(uid, earn, job_id)
        if earn > 0 and node_id:
            def _update_contrib(node=node_id, amount=earn):
                firebase_db.reference(f"/contributors/{node}").child("total_credits_earned").transaction(
                    lambda c: round((c or 0) + amount, 2)
                )
            try:
                await asyncio.to_thread(_update_contrib)
            except Exception as e:
                print(f"[db] partial pay contributor update failed: {e}")
        print(f"[coordinator] partial pay: {node_id} earned {earn} credits ({partial['duration_seconds']}s / {total_duration}s)")

@app.get("/credits/{uid}")
async def get_credits(uid: str):
    def _read():
        return firebase_db.reference(f"/credits/{uid}").get()
    try:
        data = await asyncio.to_thread(_read)
        if not data:
            return {"balance": 100.0, "total_earned": 0.0, "total_spent": 0.0}
        return {
            "balance": float(data.get("balance", 100.0)),
            "total_earned": float(data.get("total_earned", 0.0)),
            "total_spent": float(data.get("total_spent", 0.0))
        }
    except Exception as e:
        return {"balance": 100.0, "total_earned": 0.0, "total_spent": 0.0, "error": str(e)}

@app.get("/credits/{uid}/transactions")
async def get_transactions(uid: str):
    def _read():
        data = firebase_db.reference(f"/credit_transactions/{uid}").get()
        if not data:
            return []
        items = list(data.values())
        items.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return items[:20]
    try:
        data = await asyncio.to_thread(_read)
        return {"transactions": data}
    except Exception as e:
        return {"transactions": [], "error": str(e)}

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
    estimated_cost: float = 1.0
    cpu_cores: float = 2.0
    ram_gb: int = 4
    gpu_vram_gb: float = 0.0
    duration_estimate_seconds: float = 60.0
    dataset_filename: Optional[str] = None
    output_extensions: Optional[List[str]] = None

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

    submitter_uid = user.get("uid", "anonymous") if user else "anonymous"
    submitter_email = user.get("email", "anonymous") if user else "anonymous"

    if AUTH_ENABLED and submitter_uid != "anonymous":
        balance = await db_get_credit_balance(submitter_uid)
        if balance < 1.0:
            return {
                "job_id": None,
                "assigned": False,
                "rejected": True,
                "reason": "insufficient_credits",
                "balance": balance,
                "scan_violations": [],
                "message": f"Insufficient credits. Your balance is {balance} credits. Purchase more credits to continue."
            }

    job_id = str(uuid.uuid4())
    job = Job(
        job_id=job_id,
        script=req.script,
        requirements=req.requirements,
        use_gpu=req.use_gpu,
        dataset_filename=req.dataset_filename
    )
    job.submitter_uid = submitter_uid
    job.submitter_email = submitter_email
    job.output_extensions = req.output_extensions or [".pkl", ".pt", ".h5", ".csv", ".json", ".txt", ".png", ".jpg"]

    if AUTH_ENABLED and submitter_uid != "anonymous":
        estimated_cost = max(1.0, req.estimated_cost if hasattr(req, 'estimated_cost') else 1.0)
        await db_deduct_credits(submitter_uid, estimated_cost, job_id)
        job.estimated_cost = estimated_cost

    jobs[job_id] = job
    asyncio.create_task(db_upsert_user(submitter_uid, submitter_email, user.get("name", "") if user else ""))
    asyncio.create_task(db_create_job(job, submitter_uid, submitter_email))

    assigned = False
    if job.dataset_ready:
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
            job.current_contributor_start = time.time()
            try:
                await best_conn.ws.send_json({
                    "type": "job",
                    "job_id": job_id,
                    "script": job.script,
                    "requirements": job.requirements,
                    "use_gpu": job.use_gpu,
                    "resume_from_epoch": job.checkpoint_epoch,
                    "coordinator_url": COORDINATOR_HTTP,
                    "dataset_filename": getattr(job, "dataset_filename", None),
                    "output_extensions": getattr(job, "output_extensions", [".pkl", ".pt", ".h5", ".csv", ".json", ".txt", ".png", ".jpg"]),
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
    new_pending = deque()
    while pending_jobs:
        job_id = pending_jobs.popleft()
        job = jobs.get(job_id)
        if not job or job.done:
            continue

        if not job.dataset_ready:
            new_pending.append(job_id)
            continue

        available = [
            (cid, c) for cid, c in contributors.items()
            if not c.busy and (not job.use_gpu or c.has_gpu)
        ]
        if not available:
            new_pending.append(job_id)
            continue

        best_cid, best_conn = max(
            available,
            key=lambda x: score_contributor(x[1], job.use_gpu)
        )

        best_conn.busy = True
        best_conn.current_job = job_id
        job.contributor_node_id = best_conn.node_id
        job.status = "running"
        job.current_contributor_start = time.time()

        try:
            await best_conn.ws.send_json({
                "type": "job",
                "job_id": job_id,
                "script": job.script,
                "requirements": job.requirements,
                "use_gpu": job.use_gpu,
                "resume_from_epoch": job.checkpoint_epoch,
                "coordinator_url": COORDINATOR_HTTP,
                "dataset_filename": getattr(job, "dataset_filename", None),
                "output_extensions": getattr(job, "output_extensions", [".pkl", ".pt", ".h5", ".csv", ".json", ".txt", ".png", ".jpg"]),
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
            new_pending.appendleft(job_id)
            break

    pending_jobs.extendleft(reversed(list(new_pending)))

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

@app.post("/upload-dataset")
async def upload_dataset(request: Request, user=Depends(optional_verify_token)):
    body = await request.json()
    filename = body.get("filename", "dataset.bin")
    data_b64 = body.get("data")
    job_id = body.get("job_id")

    if not data_b64 or not job_id:
        return {"error": "missing data or job_id"}

    safe_filename = os.path.basename(filename)
    job_datasets_dir = os.path.join(DATASETS_DIR, job_id)
    os.makedirs(job_datasets_dir, exist_ok=True)
    dataset_path = os.path.join(job_datasets_dir, safe_filename)

    try:
        with open(dataset_path, "wb") as f:
            f.write(base64.b64decode(data_b64))
        print(f"[coordinator] dataset saved: {dataset_path}")
        
        job = jobs.get(job_id)
        if job:
            job.dataset_ready = True
            asyncio.create_task(try_assign_pending())
            
        return {"saved": True, "filename": safe_filename, "path": dataset_path}
    except Exception as e:
        return {"error": str(e)}

@app.post("/upload-output/{job_id}")
async def upload_output(job_id: str, request: Request):
    body = await request.json()
    filename = body.get("filename", "output.bin")
    data_b64 = body.get("data")

    if not data_b64:
        return {"error": "missing data"}

    safe_filename = os.path.basename(filename)
    job_output_dir = os.path.join(OUTPUTS_DIR, job_id)
    os.makedirs(job_output_dir, exist_ok=True)
    output_path = os.path.join(job_output_dir, safe_filename)
    try:
        raw_bytes = base64.b64decode(data_b64)
        with open(output_path, "wb") as f:
            f.write(raw_bytes)

        # Also store in Firebase so it survives ephemeral disk resets
        def _store_in_firebase():
            firebase_db.reference(f"/outputs/{job_id}/{safe_filename}").set({
                "filename": safe_filename,
                "size_bytes": len(raw_bytes),
                "data_b64": data_b64,
                "uploaded_at": time.time()
            })
        asyncio.create_task(asyncio.to_thread(_store_in_firebase))

        sub_ws = submitter_connections.get(job_id)
        if sub_ws:
            try:
                await sub_ws.send_json({
                    "type": "output_file",
                    "job_id": job_id,
                    "filename": safe_filename,
                    "download_url": f"/download-output/{job_id}/{safe_filename}"
                })
            except Exception:
                pass

        return {"saved": True, "filename": safe_filename}
    except Exception as e:
        return {"error": str(e)}

@app.get("/download-output/{job_id}/{filename}")
async def download_output(job_id: str, filename: str):
    from fastapi.responses import FileResponse, Response
    safe_filename = os.path.basename(filename)
    output_path = os.path.join(OUTPUTS_DIR, job_id, safe_filename)

    if os.path.exists(output_path):
        return FileResponse(output_path, filename=safe_filename)

    def _read():
        return firebase_db.reference(f"/outputs/{job_id}/{safe_filename}").get()
    try:
        fb_data = await asyncio.to_thread(_read)
        if fb_data and fb_data.get("data_b64"):
            raw = base64.b64decode(fb_data["data_b64"])
            return Response(
                content=raw,
                media_type="application/octet-stream",
                headers={"Content-Disposition": f"attachment; filename={safe_filename}"}
            )
    except Exception:
        pass

    raise HTTPException(status_code=404, detail="Output file not found")

@app.get("/list-outputs/{job_id}")
async def list_outputs(job_id: str):
    files = []
    job_output_dir = os.path.join(OUTPUTS_DIR, job_id)
    if os.path.exists(job_output_dir):
        for fname in os.listdir(job_output_dir):
            fpath = os.path.join(job_output_dir, fname)
            files.append({
                "filename": fname,
                "size_bytes": os.path.getsize(fpath),
                "download_url": f"/download-output/{job_id}/{fname}"
            })

    if not files:
        def _read():
            return firebase_db.reference(f"/outputs/{job_id}").get()
        try:
            fb_data = await asyncio.to_thread(_read)
            if fb_data:
                for fname, finfo in fb_data.items():
                    files.append({
                        "filename": finfo["filename"],
                        "size_bytes": finfo["size_bytes"],
                        "download_url": f"/download-output/{job_id}/{finfo['filename']}"
                    })
        except Exception:
            pass

    return {"files": files}

@app.get("/datasets/{job_id}/{filename}")
async def serve_dataset(job_id: str, filename: str):
    from fastapi.responses import FileResponse
    safe_filename = os.path.basename(filename)
    dataset_path = os.path.join(DATASETS_DIR, job_id, safe_filename)
    if not os.path.exists(dataset_path):
        raise HTTPException(status_code=404, detail="Dataset not found")
    return FileResponse(dataset_path)

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
                    conn.uid = uid
                    conn.gpu_vram_gb = round(conn.max_gpu_vram_mb / 1024, 2)
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
                gpu_vram_gb = msg.get("gpu_vram_gb", 0.0)
                if job_id in jobs:
                    jobs[job_id].done = True
                    jobs[job_id].status = "complete"
                    job = jobs[job_id]
                    duration = round(time.time() - (job.current_contributor_start or job.submitted_at), 1)
                    all_contributions = job.contributions[:]
                    estimated_cost = job.estimated_cost
                    submitter_uid = job.submitter_uid
                    use_gpu = job.use_gpu

                    actual_cost = calculate_job_cost(duration, conn.max_cpus, conn.max_ram_gb, use_gpu, gpu_vram_gb)
                    total_duration = sum(c["duration_seconds"] for c in all_contributions) + duration
                    pool = round(actual_cost, 2)

                    def _complete_write(
                        jid=job_id, nid=conn.node_id, dur=duration, cpus=conn.max_cpus,
                        ram=conn.max_ram_gb, gpu=use_gpu, gpu_name=conn.gpu_name or "",
                        cost=actual_cost, contrib_count=len(all_contributions) + 1
                    ):
                        firebase_db.reference(f"/jobs/{jid}").update({
                            "status": "complete",
                            "contributor_node_id": nid,
                            "completed_at": time.time(),
                            "duration_seconds": dur,
                            "total_duration_seconds": total_duration,
                            "cpu_time_seconds": round(cpus * dur, 1),
                            "gpu_time_seconds": round(dur, 1) if gpu else 0,
                            "actual_cost_credits": cost,
                            "contributor_count": contrib_count,
                        })
                        ref = firebase_db.reference(f"/contributors/{nid}")
                        existing = ref.get()
                        if existing:
                            ref.update({
                                "total_jobs_executed": (existing.get("total_jobs_executed") or 0) + 1,
                                "total_cpu_time_seconds": round((existing.get("total_cpu_time_seconds") or 0) + cpus * dur, 1),
                                "total_gpu_time_seconds": round((existing.get("total_gpu_time_seconds") or 0) + (dur if gpu else 0), 1),
                                "last_seen": time.time(),
                            })

                    asyncio.create_task(asyncio.to_thread(_complete_write))

                    if AUTH_ENABLED:
                        if submitter_uid and submitter_uid != "anonymous":
                            if estimated_cost > actual_cost:
                                refund = round(estimated_cost - actual_cost, 2)
                                asyncio.create_task(db_refund_credits(submitter_uid, refund, job_id))

                        if total_duration > 0:
                            final_fraction = duration / total_duration
                            final_earn = round(pool * final_fraction, 2)
                            if final_earn > 0 and conn.uid and conn.uid != "anonymous":
                                asyncio.create_task(db_earn_credits(conn.uid, final_earn, job_id))
                            if final_earn > 0:
                                def _update_final(nid=conn.node_id, earn=final_earn):
                                    firebase_db.reference(f"/contributors/{nid}").child("total_credits_earned").transaction(
                                        lambda c: round((c or 0) + earn, 2)
                                    )
                                asyncio.create_task(asyncio.to_thread(_update_final))

                            if all_contributions:
                                asyncio.create_task(db_pay_partial_contributors(job_id, all_contributions, actual_cost))

                conn.busy = False
                conn.current_job = None
                sub_ws = submitter_connections.get(job_id)
                if sub_ws:
                    try:
                        await sub_ws.send_json({
                            "type": "done", 
                            "job_id": job_id,
                            "actual_cost": actual_cost
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
                if job.current_contributor_start:
                    partial_duration = round(time.time() - job.current_contributor_start, 1)
                    if partial_duration > 0:
                        job.contributions.append({
                            "node_id": conn.node_id,
                            "uid": conn.uid,
                            "started_at": job.current_contributor_start,
                            "ended_at": time.time(),
                            "duration_seconds": partial_duration,
                            "cpu_cores": conn.max_cpus,
                            "ram_gb": conn.max_ram_gb,
                            "gpu_vram_gb": conn.gpu_vram_gb,
                        })
                        job.current_contributor_start = None
                        print(f"[coordinator] partial contribution recorded: {conn.node_id} worked {partial_duration}s")

                job.retry_count += 1
                if job.retry_count > job.max_retries:
                    job.status = "failed"
                    if AUTH_ENABLED and job.submitter_uid and job.submitter_uid != "anonymous":
                        asyncio.create_task(db_refund_credits(job.submitter_uid, job.estimated_cost, conn.current_job))
                        asyncio.create_task(db_pay_partial_contributors(conn.current_job, job.contributions, job.estimated_cost))
                    sub_ws = submitter_connections.get(conn.current_job)
                    if sub_ws:
                        try:
                            await sub_ws.send_json({
                                "type": "failed",
                                "job_id": conn.current_job,
                                "message": f"Job failed after {job.max_retries} attempts — credits refunded"
                            })
                        except Exception:
                            pass
                else:
                    job.status = "pending"
                    job.contributor_node_id = None
                    pending_jobs.appendleft(conn.current_job)
                    sub_ws = submitter_connections.get(conn.current_job)
                    if sub_ws:
                        try:
                            await sub_ws.send_json({
                                "type": "status",
                                "job_id": conn.current_job,
                                "status": "pending",
                                "queue_position": 1,
                                "message": f"Contributor disconnected — reassigning (attempt {job.retry_count}/{job.max_retries})...",
                                "checkpoint_epoch": job.checkpoint_epoch
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
