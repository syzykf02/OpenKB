"""Cooperative cancellation for the add/compile pipeline.

The REST API runs each ``add`` in a threadpool worker (``run_in_threadpool``),
which threads cannot be killed — so cancellation is cooperative: the API layer
sets a ``threading.Event`` and the pipeline checks it at LLM-call boundaries
(``agent/compiler.py``), where essentially all wall time is spent. PageIndex's
blocking ``Collection.add()`` has no cancellation API, so API jobs run that
single operation in a child process and stop the process when this flag is
set. On a hit, ``check_cancelled`` raises ``IngestCancelled``.

``IngestCancelled`` deliberately subclasses ``BaseException`` (like
``KeyboardInterrupt``): the pipeline is full of ``except Exception`` handlers
that log-and-continue or retry, and a user cancel must not be swallowed by
them — ``run_add_mutation`` treats it as an interrupt, rolling back the
in-flight snapshot and propagating.

The event travels through the pipeline in a ``ContextVar``: starlette's
``run_in_threadpool`` (anyio) copies the caller's context into the worker
thread, and ``asyncio.run`` copies it into each per-doc compile loop, so the
check is visible everywhere without threading a parameter through the shared
CLI/API ``add_single_file`` signature.

This module imports nothing from ``openkb`` so any module (including
``agent/compiler.py``) can import it without risking an import cycle.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar

# The active ingest job's cancel flag, when running under the REST API.
# ``None`` on the CLI path, where ``check_cancelled`` is a no-op.
cancel_event_var: ContextVar[threading.Event | None] = ContextVar(
    "openkb_ingest_cancel", default=None
)


class IngestCancelled(BaseException):
    """The user cancelled an in-flight ingest; propagates like an interrupt.

    A ``BaseException`` (not ``Exception``) so ``except Exception`` handlers
    along the pipeline (compile retries, per-stage error logging) let it pass;
    ``run_add_mutation``'s ``BaseException`` arm rolls back and re-raises.
    """


def check_cancelled() -> None:
    """Raise :class:`IngestCancelled` if the current ingest job was cancelled.

    No-op when no job is active (CLI) or the job has not been cancelled. Call
    at stage boundaries and before each LLM request.
    """
    event = cancel_event_var.get()
    if event is not None and event.is_set():
        raise IngestCancelled("ingest cancelled by user")
