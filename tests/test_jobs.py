"""Tests for server-owned jobs (openkb.jobs) and the off-loop lock bridge
(openkb.async_locks): the machinery that keeps the API responsive while long
compiles run, and lets the UI re-attach after a refresh.
"""

from __future__ import annotations

import asyncio
import threading
import time

from fastapi.testclient import TestClient

from openkb.api import create_app
from openkb.async_locks import async_kb_lock
from openkb.jobs import JobRegistry
from openkb.locks import kb_ingest_lock


def _client(monkeypatch, token: str = "secret") -> TestClient:
    monkeypatch.setenv("OPENKB_API_TOKEN", token)
    return TestClient(create_app())


def _auth(token: str = "secret") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _use_named_kb(monkeypatch, kb_dir, name: str = "test-kb") -> str:
    def resolve(kb):
        assert kb == name
        return kb_dir

    monkeypatch.setattr("openkb.api_helpers.resolve_kb_alias", resolve)
    return name


def _parse_events(text: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event, data) pairs, tolerating `id:` lines and
    keep-alive comment blocks."""
    import json

    out: list[tuple[str, dict]] = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        event = next((ln for ln in lines if ln.startswith("event: ")), None)
        data = next((ln for ln in lines if ln.startswith("data: ")), None)
        if event is not None and data is not None:
            out.append((event[len("event: ") :], json.loads(data[len("data: ") :])))
    return out


# ---------------------------------------------------------------------------
# async_kb_lock: flock waits must not freeze the event loop
# ---------------------------------------------------------------------------


def test_async_kb_lock_waits_off_loop(tmp_path):
    """While async_kb_lock waits behind a held flock, the event loop keeps
    running other tasks — the exact freeze this bridge exists to prevent."""
    openkb_dir = tmp_path / ".openkb"
    openkb_dir.mkdir()
    holder_ready = threading.Event()
    release = threading.Event()

    def holder():
        with kb_ingest_lock(openkb_dir):
            holder_ready.set()
            release.wait(10)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert holder_ready.wait(5)

    async def main():
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(4):
                await asyncio.sleep(0.05)
                ticks += 1

        async def waiter():
            async with async_kb_lock(openkb_dir, exclusive=True):
                pass

        ticker_task = asyncio.create_task(ticker())
        waiter_task = asyncio.create_task(waiter())
        await asyncio.sleep(0.1)
        assert not waiter_task.done()  # still blocked on the flock…
        assert ticks >= 1  # …yet the loop kept ticking
        release.set()
        await asyncio.wait({ticker_task, waiter_task}, timeout=10)
        assert waiter_task.done()
        assert ticks == 4

    asyncio.run(main())
    thread.join(5)


def test_async_kb_lock_serializes_concurrent_spans(tmp_path):
    """Two async spans on the same KB never overlap."""
    openkb_dir = tmp_path / ".openkb"
    openkb_dir.mkdir()
    inside = 0
    max_inside = 0

    async def span():
        nonlocal inside, max_inside
        async with async_kb_lock(openkb_dir, exclusive=True):
            inside += 1
            max_inside = max(max_inside, inside)
            await asyncio.sleep(0.02)
            inside -= 1

    async def main():
        await asyncio.gather(*(span() for _ in range(4)))

    asyncio.run(main())
    assert max_inside == 1


# ---------------------------------------------------------------------------
# JobRegistry: per-KB queueing, terminal states
# ---------------------------------------------------------------------------


def test_jobs_serialize_per_kb_with_visible_queued_state():
    """A second job on the same KB stays `queued` (visible to the UI) until
    the first finishes; the waiter's log says what it's waiting on."""

    async def main():
        registry = JobRegistry()
        order: list[str] = []
        first_running = asyncio.Event()

        async def worker_one(job):
            first_running.set()
            await asyncio.sleep(0.15)
            order.append("one")
            return {"n": 1}

        async def worker_two(job):
            order.append("two")
            return {"n": 2}

        job_one = registry.create("add", "kb-a", "add: one.md")
        registry.submit(job_one, worker_one)
        await first_running.wait()

        job_two = registry.create("add", "kb-a", "add: two.md")
        registry.submit(job_two, worker_two)
        await asyncio.sleep(0.03)
        assert job_two.status == "queued"  # UI can show "waiting"
        assert any(
            f["event"] == "log" and "Waiting" in f["data"]["message"] for f in job_two._events
        )

        deadline = time.time() + 5
        while time.time() < deadline and not job_two.terminal:
            await asyncio.sleep(0.02)
        assert order == ["one", "two"]
        assert job_one.status == "done" and job_one.result == {"n": 1}
        assert job_two.status == "done"
        # Terminal frames: final + done, with monotonic seqs.
        events = list(job_two._events)
        assert events[-1]["event"] == "done"
        assert events[-2]["event"] == "final"
        assert [f["seq"] for f in events] == list(range(len(events)))

    asyncio.run(main())


def test_job_failure_is_recorded_not_raised():
    async def main():
        registry = JobRegistry()

        async def broken(job):
            raise RuntimeError("boom")

        job = registry.create("add", "kb-b", "add: bad.md")
        registry.submit(job, broken)
        deadline = time.time() + 5
        while time.time() < deadline and not job.terminal:
            await asyncio.sleep(0.02)
        assert job.status == "failed"
        assert "boom" in (job.error or "")
        assert any(f["event"] == "error" for f in job._events)

    asyncio.run(main())


def test_subscriber_replay_and_live_delivery():
    """subscribe() replays ring frames past last_seq exactly once, then tails
    live frames — the page-refresh re-attach contract."""

    async def main():
        registry = JobRegistry()
        gate = asyncio.Event()

        async def worker(job):
            job.record("file_start", {"n": 1})
            job.record("file_start", {"n": 2})
            await gate.wait()
            job.record("file_start", {"n": 3})
            return {}

        job = registry.create("add", "kb-c", "add: x.md")
        registry.submit(job, worker)
        deadline = time.time() + 5
        while time.time() < deadline and len(job._events) < 2:
            await asyncio.sleep(0.01)

        replay, queue = job.subscribe(last_seq=-1)
        assert [f["data"]["n"] for f in replay] == [1, 2]
        gate.set()
        frame = await asyncio.wait_for(queue.get(), timeout=5)
        assert frame["data"]["n"] == 3  # live frame, not duplicated
        deadline = time.time() + 5
        while time.time() < deadline and not job.terminal:
            await asyncio.sleep(0.02)
        job.unsubscribe(queue)

    asyncio.run(main())


# ---------------------------------------------------------------------------
# API-level regression: a held KB lock must not freeze other endpoints
# ---------------------------------------------------------------------------


def test_endpoints_respond_while_kb_lock_held(monkeypatch, kb_dir):
    """With the ingest lock held (simulating a long compile), an unrelated
    endpoint still answers immediately — the loop is not blocked on the flock —
    and a recompile request waits OFF the loop until the lock frees."""
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    holder_ready = threading.Event()
    release = threading.Event()

    def holder():
        with kb_ingest_lock(kb_dir / ".openkb"):
            holder_ready.set()
            release.wait(15)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert holder_ready.wait(5)

    recompile_done: list = []

    def recompile():
        response = client.post(
            "/api/v1/recompile",
            json={"kb": kb, "all_docs": True, "stream": False},
            headers=_auth(),
        )
        recompile_done.append(response.status_code)

    try:
        recompile_thread = threading.Thread(target=recompile, daemon=True)
        recompile_thread.start()
        time.sleep(0.4)
        # Recompile is parked on the lock…
        assert not recompile_done
        # …yet the server still responds, fast.
        t0 = time.time()
        status = client.post("/api/v1/status", json={"kb": kb}, headers=_auth())
        assert status.status_code == 200
        assert time.time() - t0 < 3
        # Jobs endpoint responds too.
        assert client.get("/api/v1/jobs", params={"kb": kb}, headers=_auth()).status_code == 200
        # Free the lock: the waiting recompile proceeds (empty KB → 404 error
        # event mapped to HTTP 404 — the point is that it UNBLOCKS).
        release.set()
        recompile_thread.join(10)
        assert recompile_done == [404]
    finally:
        release.set()
        thread.join(5)


def test_job_runs_without_attached_viewer(monkeypatch, kb_dir):
    """A job needs no watcher: it runs to completion server-side with nobody
    attached (user closed the tab / refreshed), and a fresh client can still
    read its outcome and full event history afterwards — the refresh story."""
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    from openkb.cli import AddFileResult

    gate = threading.Event()

    def slow_fake_add(path, target_kb, **kwargs):
        gate.wait(15)  # hold the "compile" open until the test says otherwise
        return AddFileResult(path.name, str(path), "added", f"{path.name} added")

    monkeypatch.setattr("openkb.api_helpers._add_for_api", slow_fake_add)

    with client:
        response = client.post(
            "/api/v1/add",
            data={"kb": kb, "stream": "true"},
            files=[("files", ("paper.md", b"# Paper", "text/markdown"))],
            headers=_auth(),
        )
        job_id = response.json()["job_id"]
        # No event stream attached at all — the "user closed the tab" case.
        # The job must still be alive and working server-side.
        deadline = time.time() + 10
        while time.time() < deadline:
            status = client.get(f"/api/v1/jobs/{job_id}", headers=_auth()).json()["status"]
            if status == "running":
                break
            time.sleep(0.05)
        assert status == "running"
        gate.set()  # let the "compile" finish
        deadline = time.time() + 15
        while time.time() < deadline:
            summary = client.get(f"/api/v1/jobs/{job_id}", headers=_auth()).json()
            if summary["status"] != "running":
                break
            time.sleep(0.05)
        assert summary["status"] == "done"
        assert summary["result"]["added_count"] == 1

        # A brand-new viewer (the refreshed page) replays the full history.
        stream = client.get(f"/api/v1/jobs/{job_id}/events", headers=_auth())
        events = _parse_events(stream.text)
        names = [name for name, _ in events]
        assert names[0] == "start" and names[-1] == "done"
        assert "file_start" in names and "file_done" in names and "final" in names
        final = dict(events)["final"]
        assert final["added_count"] == 1


def test_recompile_worker_captures_compiler_logs(monkeypatch, tmp_path):
    """The recompile worker forwards compiler log records as job ``log`` frames,
    so the UI panel shows recompile progress (regression: it previously mounted
    no JobLogHandler, so the panel stayed empty)."""
    import logging

    from openkb.api_ingest import run_recompile_worker

    async def fake_iter_recompile(kb_dir, document_name, *, bundle=None):
        logging.getLogger("openkb.agent.compiler").info("recompiling page X")
        yield {"event": "doc", "status": "ok", "message": "ok"}
        yield {"event": "final", "status": "done"}

    monkeypatch.setattr("openkb.cli.iter_recompile", fake_iter_recompile)

    async def main():
        registry = JobRegistry()
        job = registry.create("recompile", "kb", "recompile: doc")
        registry.submit(job, lambda j: run_recompile_worker(j, tmp_path, "doc"))
        deadline = time.time() + 5
        while time.time() < deadline and not job.terminal:
            await asyncio.sleep(0.02)
        return job

    job = asyncio.run(main())
    assert job.status == "done"
    assert any(
        f["event"] == "log"
        and "recompiling page X" in f["data"]["message"]
        and f["data"]["logger"] == "openkb.agent.compiler"
        for f in job._events
    )
