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
) -> Job:
    """Create and submit the add job for uploaded (already saved) files.

    Returns immediately with the queued job; the caller responds with the job
    id and clients watch progress via ``/api/v1/jobs/{id}/events``.
    """
    first = saved_uploads[0][1] if saved_uploads else "?"
    title = f"add: {first}" + (f" (+{len(saved_uploads) - 1})" if len(saved_uploads) > 1 else "")
    job = registry.create("add", kb, title)
    job.record(
        "start",
        {
            "endpoint": "add",
            "kb": kb,
            "file_count": len(saved_uploads),
            "steps_per_file": 3,
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
        for file_index, (saved_path, original_name) in enumerate(saved_uploads):
            if job.cancelled:
                raise IngestCancelled("ingest cancelled by user")
            context = {"file_index": file_index, "original_name": original_name}
            job.record("uploaded", {**context, "saved_path": str(saved_path)})
            job.record(
                "file_start",
                {
                    **context,
                    "saved_path": str(saved_path),
                    "completed_steps": 0,
                    "total_steps": 3,
                    "step": "prepare",
                },
            )
            job.record(
                "file_progress",
                {
                    **context,
                    "completed_steps": 1,
                    "total_steps": 3,
                    "step": "prepare",
                    "message": "Source file is ready for compilation.",
                },
            )
            job.record(
                "log",
                {
                    "level": "info",
                    "logger": __name__,
                    "message": f"{original_name}: starting conversion, indexing, and compilation.",
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
                        "total_steps": 3,
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
                    "completed_steps": 3 if item.status in {"added", "skipped"} else 2,
                    "total_steps": 3,
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
                    "completed_steps": 3 if item.status in {"added", "skipped"} else 2,
                    "total_steps": 3,
                    "step": "finalize" if item.status in {"added", "skipped"} else "compile",
                },
            )
        return _model_payload(_summarize_add_results(kb, results))
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
