"""Server-side job registry for long-running KB operations.

Long operations (document add/compile first; recompile etc. can follow) run as
**jobs owned by the server**, not by the HTTP request that started them:

- Starting a job returns its id immediately; the client watches progress over
  a separate re-attachable SSE stream (``GET /api/v1/jobs/{id}/events``).
- Refreshing the page loses only the client's view — ``GET /api/v1/jobs``
  lists live jobs so the UI restores progress rows and logs, then re-attaches
  (the stream replays the job's event ring from a ``last_seq`` cursor).
- Closing the tab does NOT cancel anything; cancel is an explicit
  ``POST /api/v1/jobs/{id}/cancel`` that sets the job's cooperative cancel
  flag (``openkb.ingest_cancel`` checkpoints stop the worker, rolling back the
  in-flight mutation).
- Mutating jobs are serialized per KB by an asyncio lock: contending jobs stay
  visibly ``queued`` instead of pinning an HTTP request to a silent wait, and
  no request handler ever blocks on a long operation — the event loop stays
  free for every other endpoint.

Jobs live in memory (per server process): enough to survive page refreshes,
which is the failure mode this exists for. The ring buffer is bounded so a
chatty compile can't grow memory without limit; finished jobs are pruned.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from openkb.ingest_cancel import IngestCancelled

# Bounds: per-job event ring (SSE frames, log lines included) and how many
# finished jobs to keep per KB for post-refresh history.
MAX_EVENTS_PER_JOB = 2000
FINISHED_RETENTION_PER_KB = 25

JobStatus = str  # "queued" | "running" | "done" | "failed" | "cancelled"
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})


@dataclass
class Job:
    """One server-owned background operation and its event history."""

    id: str
    kind: str  # "add" | "recompile" | ... (display/grouping only)
    kb: str
    title: str  # human label, e.g. "add: paper.pdf (+2 more)"
    status: JobStatus = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    # Cooperative cancel: published into ingest_cancel.cancel_event_var for
    # the worker's span; checkpoints raise IngestCancelled when set.
    cancel_event: threading.Event = field(default_factory=threading.Event)
    # Worker-thread ident for log capture (set by the worker, read by its
    # logging handler); None until the first worker thread binds it.
    thread_id: int | None = None
    # Persistent file-task store and file ids are attached by API job creators.
    # CLI/internal users can leave both empty and retain the lightweight
    # in-memory registry behavior.
    store: Any | None = None
    file_ids: list[str] = field(default_factory=list)

    _seq: itertools.count = field(default_factory=lambda: itertools.count())
    _events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_EVENTS_PER_JOB))
    _subscribers: set[asyncio.Queue] = field(default_factory=set)

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def last_seq(self) -> int:
        """Seq of the newest recorded frame, or -1 if none yet."""
        return self._events[-1]["seq"] if self._events else -1

    def cancel(self) -> bool:
        """Request cancellation. Returns False if the job already finished."""
        if self.terminal:
            return False
        self.cancel_event.set()
        return True

    def record(self, event: str, data: dict[str, Any]) -> None:
        """Append one SSE frame to the ring and fan it out to subscribers.

        Loop-thread only (workers bridge here via ``call_soon_threadsafe``):
        subscribers are plain unbounded queues drained by the SSE generators.
        """
        frame = {"seq": next(self._seq), "event": event, "data": data}
        self._events.append(frame)
        if self.store is not None:
            self.store.record_event(self, event, data)
        for queue in list(self._subscribers):
            queue.put_nowait(frame)

    def emit_threadsafe(self, event: str, data: dict[str, Any], loop) -> None:
        """``record`` from a worker thread."""
        try:
            loop.call_soon_threadsafe(self.record, event, data)
        except RuntimeError:
            pass  # loop closed — server shutting down; drop the frame

    def subscribe(self, last_seq: int) -> tuple[list[dict[str, Any]], asyncio.Queue]:
        """Return (replayed frames with seq > last_seq, live-frame queue).

        Loop-thread only, and fully synchronous: no frame can interleave
        between the ring snapshot and the subscriber add, so exactly-once
        delivery holds with a plain ``seq > last_seq`` filter.
        """
        replay = [f for f in self._events if f["seq"] > last_seq]
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return replay, queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def summary(self) -> dict[str, Any]:
        """Public snapshot for ``GET /api/v1/jobs`` (no event history)."""
        return {
            "id": self.id,
            "kind": self.kind,
            "kb": self.kb,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
            "last_seq": self.last_seq,
        }


class JobRegistry:
    """All jobs of one server instance, with per-KB serialization."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._kb_serializers: dict[str, asyncio.Lock] = {}

    def create(self, kind: str, kb: str, title: str, *, store=None) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, kb=kb, title=title, store=store)
        self._jobs[job.id] = job
        self._prune_finished(kb)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, kb: str | None = None, *, active_only: bool = False) -> list[Job]:
        jobs = list(self._jobs.values())
        if kb is not None:
            jobs = [j for j in jobs if j.kb == kb]
        if active_only:
            jobs = [j for j in jobs if not j.terminal]
        return sorted(jobs, key=lambda j: j.created_at)

    def submit(self, job: Job, worker: Callable[[Job], Awaitable[dict[str, Any] | None]]) -> None:
        """Spawn the serialized runner task for a freshly created job."""
        asyncio.get_running_loop().create_task(self._run_serialized(job, worker))

    async def _run_serialized(
        self, job: Job, worker: Callable[[Job], Awaitable[dict[str, Any] | None]]
    ) -> None:
        """Run one job, serialized per KB: queued → running → terminal.

        Terminal status derives from how the worker exits: normal return →
        ``done`` (return value becomes ``job.result`` + the ``final`` frame);
        ``IngestCancelled`` → ``cancelled``; anything else → ``failed``. The
        worker records its own progress frames (logs, per-file events) as it
        goes; this wrapper owns the terminal frames and bookkeeping.
        """
        serializer = self._kb_serializers.setdefault(job.kb, asyncio.Lock())
        if serializer.locked():
            job.record(
                "log",
                {
                    "level": "info",
                    "message": "Waiting for other operations on this KB to finish…",
                    "logger": "openkb.jobs",
                },
            )
        async with serializer:
            if job.cancelled:
                job.status = "cancelled"
                job.finished_at = time.time()
                if job.store is not None:
                    job.store.update_job(job)
                job.record("cancelled", {"message": "Cancelled while queued"})
                job.record("done", {"status": job.status})
                return
            job.status = "running"
            job.started_at = time.time()
            if job.store is not None:
                job.store.update_job(job)
            try:
                result = await worker(job)
            except IngestCancelled:
                job.status = "cancelled"
                job.record("cancelled", {"message": "Cancelled by user"})
            except Exception as exc:
                job.status = "failed"
                job.error = str(exc)
                job.record("error", {"message": f"{job.kind} failed: {exc}"})
            else:
                job.status = "done"
                job.result = result
                job.record("final", result or {})
            finally:
                job.finished_at = time.time()
                if job.store is not None:
                    job.store.update_job(job)
                job.record("done", {"status": job.status})

    def _prune_finished(self, kb: str) -> None:
        finished = [j for j in self._jobs.values() if j.kb == kb and j.terminal]
        finished.sort(key=lambda j: j.finished_at or 0)
        for stale in finished[:-FINISHED_RETENTION_PER_KB]:
            if not stale._subscribers:  # keep jobs a client is still tailing
                self._jobs.pop(stale.id, None)
