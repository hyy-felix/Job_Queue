"""
Job_Queue FastAPI server.
Routes, SSE events, and static file serving.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import config
from models import JobStatus, ExtractionEditRequest
from orchestrator import Orchestrator, StateTransitionError
from persistence import JsonJobStore, JobNotFoundError

# ── Logging setup ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.JOBS_DIR / "app.log"),
    ],
)
logger = logging.getLogger("job_queue")

# ── App state ─────────────────────────────────────────────────
store = JsonJobStore()
orchestrator = Orchestrator(store)
event_queue: asyncio.Queue = asyncio.Queue()
sse_clients: list[asyncio.Queue] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: crash recovery + start generation loop. Shutdown: stop loop."""
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    orchestrator.set_event_queue(event_queue)
    await orchestrator.start()
    # Start event broadcaster
    broadcaster_task = asyncio.create_task(_event_broadcaster())
    yield
    await orchestrator.stop()
    broadcaster_task.cancel()


app = FastAPI(title="Job_Queue", lifespan=lifespan)


# ── SSE event broadcaster ────────────────────────────────────

async def _event_broadcaster():
    """Read from orchestrator event queue, broadcast to all SSE clients."""
    while True:
        event = await event_queue.get()
        dead_clients = []
        for client_queue in sse_clients:
            try:
                client_queue.put_nowait(event)
            except asyncio.QueueFull:
                dead_clients.append(client_queue)
        for dead in dead_clients:
            sse_clients.remove(dead)


# ── Request/Response models ───────────────────────────────────

class SubmitRequest(BaseModel):
    urls: list[str]


# ── Routes ────────────────────────────────────────────────────
# All mutations go through orchestrator methods.
# server.py only reads from store directly (list, get).

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.post("/api/jobs")
async def submit_jobs(req: SubmitRequest):
    if not req.urls:
        raise HTTPException(status_code=422, detail="No URLs provided")
    try:
        jobs = await orchestrator.submit_urls(req.urls)
        return {"jobs": [
            {"job_id": j.job_id, "source_url": j.source_url, "status": j.status.value}
            for j in jobs
        ]}
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/api/jobs")
async def list_jobs():
    jobs = store.list_jobs()
    counts = {}
    for s in JobStatus:
        counts[s.value] = sum(1 for j in jobs if j.status == s)
    return {
        "jobs": [j.model_dump(mode="json") for j in jobs],
        "counts": counts,
        "total": len(jobs),
    }


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    try:
        job = store.get_job(job_id)
        return job.model_dump(mode="json")
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found")


@app.put("/api/jobs/{job_id}")
async def edit_job(job_id: str, req: ExtractionEditRequest):
    try:
        job = orchestrator.edit_job(job_id, req.model_dump(exclude_none=True))
        return job.model_dump(mode="json")
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found")
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/jobs/{job_id}/approve")
async def approve_job(job_id: str):
    try:
        job = await orchestrator.approve_job(job_id)
        return job.model_dump(mode="json")
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found")
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str):
    try:
        job = await orchestrator.retry_job(job_id)
        return job.model_dump(mode="json")
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found")
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/jobs/{job_id}/apply")
async def apply_job(job_id: str):
    """Manually trigger application for a completed job.

    Only allowed from COMPLETED or APPLY_FAILED states.
    Does NOT auto-trigger — requires explicit user action.
    """
    try:
        job = await orchestrator.queue_apply(job_id)
        return job.model_dump(mode="json")
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found")
    except FileNotFoundError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    try:
        job = await orchestrator.cancel_job(job_id)
        return job.model_dump(mode="json")
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found")
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    try:
        orchestrator.delete_job(job_id)
        return JSONResponse(status_code=204, content=None)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found")
    except StateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/api/jobs/{job_id}/logs/{log_type}")
async def get_log(job_id: str, log_type: str):
    if log_type not in ("extraction", "generation", "apply"):
        raise HTTPException(status_code=400, detail="Invalid log type")

    job_dir = config.JOBS_DIR / job_id
    log_path = job_dir / "run" / f"{log_type}.log"

    if not log_path.exists():
        return {"log": ""}

    return {"log": log_path.read_text(encoding="utf-8", errors="replace")}


@app.get("/api/jobs/{job_id}/outputs")
async def get_outputs(job_id: str):
    job_dir = config.JOBS_DIR / job_id / "output"
    if not job_dir.exists():
        return {"files": []}

    files = []
    for f in job_dir.iterdir():
        if f.is_file():
            files.append({
                "name": f.name,
                "size": f.stat().st_size,
                "path": str(f),
            })
    return {"files": files}


@app.get("/api/events")
async def sse_events(request: Request):
    client_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    sse_clients.append(client_queue)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(client_queue.get(), timeout=15)
                    yield {
                        "event": event["event"],
                        "data": json.dumps(event["data"]),
                    }
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
        finally:
            if client_queue in sse_clients:
                sse_clients.remove(client_queue)

    return EventSourceResponse(event_generator())


# ── Main ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)
