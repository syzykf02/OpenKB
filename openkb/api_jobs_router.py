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
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from openkb.api_helpers import _resolve_kb, require_bearer_token
from openkb.api_ingest import start_add_job
from openkb.api_models import JobRetryRequest
from openkb.config import resolve_credential_bundle
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


def _retry_source_file(job: Job, file_index: int, raw_dir: Path) -> tuple[Path, str]:
    """Return one failed add file retained in ``raw/``, or raise a safe 4xx.

    The source path comes from this job's own replayable ``uploaded`` event,
    never from the request. It is resolved and constrained below ``raw/`` so a
    stale or malformed in-memory event cannot be used to read arbitrary files.
    """
    uploaded = next(
        (
            frame["data"]
            for frame in job._events
            if frame["event"] == "uploaded" and frame["data"].get("file_index") == file_index
        ),
        None,
    )
    completed = next(
        (
            frame["data"]
            for frame in job._events
            if frame["event"] == "file_done" and frame["data"].get("file_index") == file_index
        ),
        None,
    )
    if uploaded is None or completed is None or completed.get("status") != "failed":
        raise HTTPException(status_code=409, detail="Only failed add files can be retried.")
    original_name = uploaded.get("original_name")
    saved_path = uploaded.get("saved_path")
    if not isinstance(original_name, str) or not isinstance(saved_path, str):
        raise HTTPException(status_code=409, detail="The failed file cannot be retried.")
    raw_root = raw_dir.resolve()
    candidate = Path(saved_path).resolve()
    if not candidate.is_relative_to(raw_root) or not candidate.is_file():
        raise HTTPException(status_code=409, detail="The retained source file is no longer available.")
    return candidate, original_name


@jobs_router.post("/api/v1/jobs/{job_id}/retry")
async def retry_job_file_endpoint(
    request: Request,
    job_id: str,
    body: JobRetryRequest,
    _: None = Depends(require_bearer_token),
) -> dict[str, Any]:
    """Start a fresh add job for one failed file retained in the KB's raw dir."""
    source_job = _job_or_404(_registry(request), job_id)
    if source_job.kind != "add" or source_job.kb != body.kb:
        raise HTTPException(status_code=404, detail="Add job not found for this knowledge base.")
    kb_dir = _resolve_kb(body.kb)
    source_file, original_name = _retry_source_file(source_job, body.file_index, kb_dir / "raw")
    retry_job = start_add_job(
        _registry(request),
        body.kb,
        kb_dir,
        [(source_file, original_name)],
        bundle=resolve_credential_bundle(kb_dir),
    )
    return {
        "job_id": retry_job.id,
        "kb": body.kb,
        "status": retry_job.status,
        "retry_of": source_job.id,
        "file_index": body.file_index,
    }


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
