"""Async bridge for the KB filesystem locks.

``kb_lock`` (``openkb.locks``) is a blocking ``portalocker`` flock. Acquiring
it directly from an ``async def`` API handler freezes the entire event loop
while it waits — one long compile would stall every request the server handles
(all KBs, the static UI, chat). This module bridges the lock onto a dedicated
holder thread so:

- the blocking ``flock`` wait happens OFF the event loop (other requests keep
  being served while a job waits its turn), and
- mutual exclusion semantics are unchanged — the lock is still the same
  ``portalocker`` file lock, held for exactly the wrapped span, with journal
  drain on exclusive acquisition.

The holder thread stays parked inside ``with kb_lock(...)`` until the async
body finishes (signalled via ``threading.Event``), then releases. Note the
lock is therefore owned by the holder thread: code inside the span must not
call ``kb_ingest_lock_held()`` from the event-loop thread (it tracks
``threading.local`` per thread) nor nest another ``kb_lock`` acquisition on
the loop thread — the mutation primitives used by these spans (atomic writes,
the compiler) do neither.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator
from pathlib import Path

from openkb.locks import kb_lock


@contextlib.asynccontextmanager
async def async_kb_lock(openkb_dir: Path, *, exclusive: bool) -> AsyncIterator[None]:
    """Hold ``kb_lock(openkb_dir, exclusive=...)`` across an async span.

    Acquisition (which may block for minutes behind another mutation) runs on
    a dedicated thread; the event loop only awaits an ``asyncio.Event``.
    Release happens on a threadpool thread so the final flock release + any
    journal I/O never blocks the loop either.
    """
    loop = asyncio.get_running_loop()
    acquired = asyncio.Event()
    release = threading.Event()
    holder_error: list[BaseException] = []

    def _holder() -> None:
        try:
            with kb_lock(openkb_dir, exclusive=exclusive):
                loop.call_soon_threadsafe(acquired.set)
                release.wait()
        except BaseException as exc:  # propagate lock failures to the awaiter
            holder_error.append(exc)
            loop.call_soon_threadsafe(acquired.set)

    holder = threading.Thread(target=_holder, name=f"kb-lock-{openkb_dir.name}", daemon=True)
    holder.start()
    try:
        await acquired.wait()
        if holder_error:
            raise holder_error[0]
        yield
    finally:
        release.set()
        # Join off-loop: the holder exits promptly (flock release is fast),
        # but never block the event loop on it.
        await asyncio.get_running_loop().run_in_executor(None, holder.join)
