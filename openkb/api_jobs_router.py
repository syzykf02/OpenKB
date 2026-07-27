"""Job REST endpoints: list server-owned jobs, tail one job's event stream,
cancel a job.

Jobs live in ``request.app.state.jobs_registry`` (an ``openkb.jobs.JobRegistry``
created per app in ``create_app``). The event stream is re-attachable: frames
carry SSE ``id:`` sequence numbers and ``?last_seq=N`` replays the job's ring
buffer from there, so a page refresh restores progress without losing events.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from openkb.api_helpers import require_bearer_token
from openkb.jobs import Job, JobRegistry

jobs_router = APIRouter()


def _registry(request: Request) -> JobRegistry:
    return request.app.state.jobs_registry


def _job_or_404(registry: JobRegistry, job_id: str) -> Job:
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return job


def _job_sse(frame: dict[str, Any]) -> str:
    """One SSE frame with the job-event seq as the SSE id (re-attach cursor)."""
    payload = json.dumps(frame["data"], ensure_ascii=False)
    return f"id: {frame['seq']}\nevent: {frame['event']}\ndata: {payload}\n\n"


@jobs_router.get("/api/v1/jobs")
async def list_jobs_endpoint(
    request: Request,
    kb: str | None = Query(default=None),
    active_only: bool = Query(default=False),
    _: None = Depends(require_bearer_token),
) -> dict[str, Any]:
    """Snapshot of job summaries (no event history); newest created last."""
    jobs = _registry(request).list(kb, active_only=active_only)
    return {"jobs": [job.summary() for job in jobs]}


@jobs_router.get("/api/v1/jobs/{job_id}")
async def job_detail_endpoint(
    request: Request,
    job_id: str,
    _: None = Depends(require_bearer_token),
) -> dict[str, Any]:
    return _job_or_404(_registry(request), job_id).summary()


@jobs_router.post("/api/v1/jobs/{job_id}/cancel")
async def cancel_job_endpoint(
    request: Request,
    job_id: str,
    _: None = Depends(require_bearer_token),
) -> dict[str, Any]:
    """Set the job's cooperative cancel flag; the worker stops at its next
    checkpoint (rolling back the in-flight mutation). Idempotent: cancelling a
    finished job is a no-op reporting its terminal status."""
    job = _job_or_404(_registry(request), job_id)
    job.cancel()
    return {"id": job.id, "cancel_requested": True, "status": job.status}


@jobs_router.get("/api/v1/jobs/{job_id}/events")
async def job_events_endpoint(
    request: Request,
    job_id: str,
    last_seq: int = Query(default=-1),
    _: None = Depends(require_bearer_token),
) -> Any:
    """Re-attachable SSE view of one job: replays ring-buffer frames with
    seq > last_seq, then tails live frames until the job's terminal ``done``
    frame (or client disconnect — which does NOT cancel the job)."""
    job = _job_or_404(_registry(request), job_id)
    return StreamingResponse(
        _job_event_stream(job, last_seq, request), media_type="text/event-stream"
    )


async def _job_event_stream(job: Job, last_seq: int, request: Request) -> AsyncIterator[str]:
    replay, queue = job.subscribe(last_seq)
    getter = asyncio.ensure_future(queue.get())
    try:
        for frame in replay:
            yield _job_sse(frame)
            if frame["event"] == "done":
                return
        # subscribe() atomically snapshots-then-subscribes, so if the job was
        # already terminal every frame (incl. `done`) was in the replay above.
        if job.terminal:
            return
        while True:
            if getter.done():
                frame = getter.result()
                yield _job_sse(frame)
                if frame["event"] == "done":
                    return
                getter = asyncio.ensure_future(queue.get())
            elif await request.is_disconnected():
                return
            else:
                await asyncio.sleep(0.5)
                yield ": keep-alive\n\n"
    finally:
        getter.cancel()
        job.unsubscribe(queue)
