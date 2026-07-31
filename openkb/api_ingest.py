"""Document-add job worker for the REST API.

``POST /api/v1/add`` (stream mode) no longer ties the compile to the HTTP
request: it saves the uploads, then starts a **server-owned job** via
``openkb.jobs.JobRegistry`` and returns the job id. This module supplies the
job's worker coroutine and its live-log capture:

- ``run_add_worker`` drives the per-file adds, recording SSE frames on the job
  (``uploaded`` / ``file_start`` / ``file_done`` + ``log`` lines) so any
  attached client — including one that re-attaches after a page refresh —
  sees the same history from the job's event ring.
- A ``logging.Handler`` attached to the ``openkb`` logger for the worker's
  lifetime forwards records emitted *by the job's own worker thread*
  (thread-id filtered, so concurrent jobs for other KBs don't mix) as ``log``
  frames. The logger's level is raised to INFO for the job, reference-counted
  so overlapping jobs restore it correctly.
- Cancellation is the job's cooperative flag (``Job.cancel_event``), published
  through ``openkb.ingest_cancel.cancel_event_var``: anyio's threadpool and
  ``asyncio.run`` both copy the context, so ``check_cancelled`` inside the
  compile pipeline sees it without signature changes, raises
  ``IngestCancelled``, and the mutation system rolls back.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool

from openkb import api_helpers
from openkb.api_helpers import _model_payload, _summarize_add_results
from openkb.api_models import AddFileItem
from openkb.ingest_cancel import IngestCancelled, cancel_event_var
from openkb.jobs import Job, JobRegistry

logger = logging.getLogger(__name__)

_OPENKB_LOGGER_NAME = "openkb"

# Reference count for the temporary INFO level on the "openkb" logger: the
# first job raises it, the last job to finish restores whatever was there
# before, so overlapping adds (different KBs) don't fight over it.
_log_level_depth = 0
_log_level_saved: int | None = None


def _raise_openkb_log_level() -> None:
    global _log_level_depth, _log_level_saved
    if _log_level_depth == 0:
        target = logging.getLogger(_OPENKB_LOGGER_NAME)
        _log_level_saved = target.level
        if target.level == logging.NOTSET or target.level > logging.INFO:
            target.setLevel(logging.INFO)
    _log_level_depth += 1


def _restore_openkb_log_level() -> None:
    global _log_level_depth, _log_level_saved
    _log_level_depth = max(0, _log_level_depth - 1)
    if _log_level_depth == 0 and _log_level_saved is not None:
        logging.getLogger(_OPENKB_LOGGER_NAME).setLevel(_log_level_saved)


class JobLogHandler(logging.Handler):
    """Forwards the worker thread's ``openkb.*`` log records onto a job."""

    def __init__(self, job: Job, loop) -> None:
        super().__init__(level=logging.INFO)
        self.job = job
        self.loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        # Only this job's worker thread — concurrent jobs (other KBs) share the
        # same "openkb" logger and must not see each other's logs.
        if record.thread != self.job.thread_id:
            return
        try:
            message = record.getMessage()
        except Exception:  # a bad %s arg must never break the ingest
            return
        self.job.emit_threadsafe(
            "log",
            {
                "level": record.levelname.lower(),
                "message": message,
                "logger": record.name,
            },
            self.loop,
        )


def start_add_job(
    registry: JobRegistry,
    kb: str,
    kb_dir: Path,
    saved_uploads: list[tuple[Path, str]],
    *,
    bundle=None,
    store=None,
    existing_file_ids: list[str] | None = None,
) -> Job:
    """Create and submit the add job for uploaded (already saved) files.

    Returns immediately with the queued job; the caller responds with the job
    id and clients watch progress via ``/api/v1/jobs/{id}/events``.
    """
    first = saved_uploads[0][1] if saved_uploads else "?"
    title = f"add: {first}" + (f" (+{len(saved_uploads) - 1})" if len(saved_uploads) > 1 else "")
    job = registry.create("add", kb, title, store=store)
    if store is not None:
        job.file_ids = store.create_job(
            job.id,
            kind="add",
            title=title,
            files=saved_uploads,
            existing_file_ids=existing_file_ids,
        )
    job.record(
        "start",
        {
            "endpoint": "add",
            "kb": kb,
            "file_count": len(saved_uploads),
            "steps_per_file": 4,
        },
    )
    job.record(
        "log",
        {
            "level": "info",
            "logger": __name__,
            "message": (
                f"Accepted {len(saved_uploads)} file(s); compilation will run serially "
                "to protect this knowledge base."
            ),
        },
    )
    registry.submit(
        job,
        lambda j: run_add_worker(j, kb, kb_dir, saved_uploads, bundle=bundle),
    )
    return job


async def run_add_worker(
    job: Job,
    kb: str,
    kb_dir: Path,
    saved_uploads: list[tuple[Path, str]],
    *,
    bundle=None,
) -> dict[str, Any]:
    """The add job body: compile each file, recording progress on the job.

    Returns the ``AddResponse`` payload (recorded as the ``final`` frame by
    the registry). Cancellation raises ``IngestCancelled`` out of the worker;
    the registry maps it to the job's ``cancelled`` status.
    """
    loop = asyncio.get_running_loop()
    handler = JobLogHandler(job, loop)
    _raise_openkb_log_level()
    logging.getLogger(_OPENKB_LOGGER_NAME).addHandler(handler)
    token = cancel_event_var.set(job.cancel_event)
    results: list[AddFileItem] = []
    try:
        job.record(
            "log",
            {
                "level": "info",
                "logger": __name__,
                "message": f"Worker started for {len(saved_uploads)} file(s).",
            },
        )
        for file_index, (saved_path, original_name) in enumerate(saved_uploads):
            if job.cancelled:
                raise IngestCancelled("ingest cancelled by user")
            context = {"file_index": file_index, "original_name": original_name}
            file_size = saved_path.stat().st_size if saved_path.exists() else 0
            job.record("uploaded", {**context, "saved_path": str(saved_path)})
            job.record(
                "log",
                {
                    "level": "info",
                    "logger": __name__,
                    "message": (
                        f"[{file_index + 1}/{len(saved_uploads)}] Received "
                        f"{original_name} ({file_size:,} bytes)."
                    ),
                },
            )
            job.record(
                "file_start",
                {
                    **context,
                    "saved_path": str(saved_path),
                    "completed_steps": 0,
                    "total_steps": 4,
                    "step": "prepare",
                },
            )
            job.record(
                "file_progress",
                {
                    **context,
                    "completed_steps": 1,
                    "total_steps": 4,
                    "step": "prepare",
                    "message": "Upload stored; preparing source conversion.",
                },
            )
            job.record(
                "log",
                {
                    "level": "info",
                    "logger": __name__,
                    "message": (
                        f"{original_name}: source conversion started; live compiler events "
                        "will follow."
                    ),
                },
            )
            job.record(
                "file_progress",
                {
                    **context,
                    "completed_steps": 2,
                    "total_steps": 4,
                    "step": "compile",
                    "message": "Converting source and compiling knowledge-base pages.",
                },
            )
            try:
                item = await _add_file_for_job(
                    job, kb_dir, saved_path, original_name, bundle=bundle
                )
            except IngestCancelled:
                # The mutation system already rolled this file's partial writes
                # back; report it, then propagate so the job turns `cancelled`.
                job.record(
                    "file_done",
                    {
                        **context,
                        "original_name": original_name,
                        "saved_path": None,
                        "status": "cancelled",
                        "message": "Cancelled by user",
                        "completed_steps": 1,
                        "total_steps": 4,
                        "step": "compile",
                    },
                )
                raise
            results.append(item)
            payload = _model_payload(item)
            job.record(
                "file_progress",
                {
                    **context,
                    "completed_steps": 4 if item.status in {"added", "skipped"} else 2,
                    "total_steps": 4,
                    "step": "finalize" if item.status in {"added", "skipped"} else "compile",
                    "message": item.message,
                },
            )
            job.record(
                "log",
                {
                    "level": "info" if item.status in {"added", "skipped"} else "error",
                    "logger": __name__,
                    "message": f"{original_name}: {item.message}",
                },
            )
            job.record(
                "file_done",
                {
                    **payload,
                    **context,
                    "completed_steps": 4 if item.status in {"added", "skipped"} else 2,
                    "total_steps": 4,
                    "step": "finalize" if item.status in {"added", "skipped"} else "compile",
                },
            )
        summary = _summarize_add_results(kb, results)
        job.record(
            "log",
            {
                "level": "info",
                "logger": __name__,
                "message": (
                    f"Batch complete: {summary.added_count} compiled, "
                    f"{summary.skipped_count} skipped, {summary.failed_count} failed."
                ),
            },
        )
        return _model_payload(summary)
    finally:
        cancel_event_var.reset(token)
        logging.getLogger(_OPENKB_LOGGER_NAME).removeHandler(handler)
        _restore_openkb_log_level()


async def _add_file_for_job(
    job: Job,
    kb_dir: Path,
    saved_path: Path,
    original_name: str,
    *,
    bundle=None,
) -> AddFileItem:
    """Run one locked add pipeline in the threadpool, bound to the job.

    Binds the worker thread ident before running so the job's log handler
    forwards records from exactly this add (thread-id filter). Calls
    ``api_helpers._add_for_api`` by module attribute (not a bound import) so
    tests can monkeypatch ``openkb.api_helpers._add_for_api``.
    """

    def _work():
        job.thread_id = threading.get_ident()
        return api_helpers._add_for_api(saved_path, kb_dir, bundle=bundle)

    result = await run_in_threadpool(_work)
    item = AddFileItem(**result.__dict__)
    item.original_name = original_name
    if item.status == "skipped":
        saved_path.unlink(missing_ok=True)
        item.saved_path = None
    return item


def start_recompile_job(
    registry: JobRegistry,
    kb: str,
    kb_dir: Path,
    *,
    file_id: str,
    document_name: str,
    store,
    bundle=None,
) -> Job:
    """Create a persistent recompile job for one already-indexed source."""
    title = f"recompile: {document_name}"
    job = registry.create("recompile", kb, title, store=store)
    job.file_ids = store.create_job(
        job.id,
        kind="recompile",
        title=title,
        files=[],
        existing_file_ids=[file_id],
    )
    job.record("start", {"endpoint": "recompile", "kb": kb, "file_count": 1})
    registry.submit(
        job,
        lambda current: run_recompile_worker(current, kb_dir, document_name, bundle=bundle),
    )
    return job


async def run_recompile_worker(
    job: Job, kb_dir: Path, document_name: str, *, bundle=None
) -> dict[str, Any]:
    """Run the existing recompile pipeline while projecting it onto one file row."""
    from openkb.cli import iter_recompile

    token = cancel_event_var.set(job.cancel_event)
    try:
        job.record(
            "file_start",
            {
                "file_index": 0,
                "original_name": document_name,
                "completed_steps": 1,
                "total_steps": 4,
                "step": "compile",
            },
        )
        job.record(
            "file_progress",
            {
                "file_index": 0,
                "completed_steps": 2,
                "total_steps": 4,
                "step": "compile",
                "message": "Recompiling knowledge-base pages.",
            },
        )
        final: dict[str, Any] = {}
        async for event in iter_recompile(kb_dir, document_name, bundle=bundle):
            event_name = event.get("event")
            if event_name == "error":
                raise RuntimeError(str(event.get("message", "Recompile failed.")))
            if event_name == "doc":
                ok = event.get("status") == "ok"
                job.record(
                    "file_done",
                    {
                        "file_index": 0,
                        "original_name": document_name,
                        "status": "added" if ok else "failed",
                        "message": event.get("message")
                        or ("Recompiled." if ok else "Recompile failed."),
                        "completed_steps": 4 if ok else 2,
                        "total_steps": 4,
                        "step": "finalize" if ok else "compile",
                    },
                )
            elif event_name == "final":
                final = dict(event)
        return final
    finally:
        cancel_event_var.reset(token)
