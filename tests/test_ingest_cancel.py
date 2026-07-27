"""Unit tests for the cooperative cancellation primitive (openkb.ingest_cancel)."""

from __future__ import annotations

import threading

import pytest

from openkb.ingest_cancel import IngestCancelled, cancel_event_var, check_cancelled


def test_check_cancelled_is_noop_without_active_job():
    # CLI path: no cancel event in context — checkpoints must be free no-ops.
    assert check_cancelled() is None


def test_check_cancelled_raises_when_flag_set():
    event = threading.Event()
    token = cancel_event_var.set(event)
    try:
        assert check_cancelled() is None  # not cancelled yet
        event.set()
        with pytest.raises(IngestCancelled):
            check_cancelled()
    finally:
        cancel_event_var.reset(token)


def test_ingest_cancelled_is_base_exception():
    # Must not be swallowed by the pipeline's many `except Exception` handlers
    # (compile retries etc.); run_add_mutation's BaseException arm rolls back.
    assert issubclass(IngestCancelled, BaseException)
    assert not issubclass(IngestCancelled, Exception)
