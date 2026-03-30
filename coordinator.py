"""
Coordinator Server — the central message router.

Runs on port 8000. Contributors connect via WebSocket to register as compute
nodes. Submitters POST scripts and then connect via WebSocket to stream logs.
"""

import uuid
import json
from typing import Dict, Optional
from collections import deque
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from scanner import scan_code, format_scan_result

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


class Job:
    def __init__(self, job_id: str, script: str, requirements: str = None, use_gpu: bool = False):
        self.job_id = job_id
        self.script = script
        self.requirements = requirements
        self.use_gpu = use_gpu
        self.done = False
        self.status = "pending"
        self.contributor_node_id: Optional[str] = None
        self.submitted_at = time.time()


contributors: Dict[int, ContributorConnection] = {}
jobs: Dict[str, Job] = {}
submitter_connections: Dict[str, WebSocket] = {}
pending_jobs: deque = deque()

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


@app.post("/submit-job")
async def submit_job(req: SubmitJobRequest):
    
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
    jobs[job_id] = job

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
            })
            assigned = True
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
            })
            
            sub_ws = submitter_connections.get(job_id)
            if sub_ws:
                try:
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



@app.websocket("/ws/contributor")
async def ws_contributor(ws: WebSocket):
    await ws.accept()
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
                conn.node_id = msg.get("node_id")
                conn.cpu_free = msg.get("cpu_free", 0)
                conn.ram_free = msg.get("ram_free", 0)
                conn.has_gpu = msg.get("has_gpu", False)
                conn.gpu_name = msg.get("gpu_name")
                conn.gpu_vram_free_mb = msg.get("gpu_vram_free_mb", 0)
                conn.gpu_vram_total_mb = msg.get("gpu_vram_total_mb", 0)
                conn.gpu_utilization = msg.get("gpu_utilization", 0)
                conn.max_gpu_vram_mb = msg.get("max_gpu_vram_mb", 0)
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
                job.status = "pending"
                job.contributor_node_id = None
                pending_jobs.appendleft(conn.current_job)
                print(f"[coordinator] contributor dropped mid-job — requeued {conn.current_job}")
                
                sub_ws = submitter_connections.get(conn.current_job)
                if sub_ws:
                    try:
                        await sub_ws.send_json({
                            "type": "status",
                            "job_id": conn.current_job,
                            "status": "pending",
                            "queue_position": 1,
                            "message": "Contributor disconnected — reassigning your job..."
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
