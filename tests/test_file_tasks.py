"""Regression tests for the JSON-backed, file-first task store."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openkb.file_tasks import FileTaskStateError, FileTaskStore
from openkb.jobs import JobRegistry


def _kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / ".openkb").mkdir(parents=True)
    (kb / "raw").mkdir()
    return kb


def test_file_task_store_persists_file_progress_and_logs(tmp_path):
    kb = _kb(tmp_path)
    source = kb / "raw" / "paper.md"
    source.write_text("# Paper", encoding="utf-8")
    store = FileTaskStore(kb)

    async def run():
        registry = JobRegistry()
        job = registry.create("add", "demo", "add: paper.md", store=store)
        job.file_ids = store.create_job(
            job.id, kind="add", title=job.title, files=[(source, source.name)]
        )

        async def worker(current):
            current.record("file_start", {"file_index": 0, "step": "prepare"})
            current.record(
                "file_progress",
                {
                    "file_index": 0,
                    "step": "compile",
                    "completed_steps": 2,
                    "total_steps": 4,
                    "message": "Compiling",
                },
            )
            current.record("log", {"level": "info", "message": "model call"})
            current.record(
                "file_done",
                {
                    "file_index": 0,
                    "status": "added",
                    "step": "finalize",
                    "completed_steps": 4,
                    "total_steps": 4,
                    "message": "Added",
                },
            )
            return {"ok": True}

        registry.submit(job, worker)
        while not job.terminal:
            await asyncio.sleep(0.01)
        return job.file_ids[0]

    file_id = asyncio.run(run())
    restored = FileTaskStore(kb).get_file(file_id)
    assert restored is not None
    assert restored["status"] == "succeeded"
    assert restored["step"] == "finalize"
    assert restored["history"][-1]["logs"][-1]["message"] == "model call"
    saved = json.loads((kb / ".openkb" / "file-tasks.json").read_text(encoding="utf-8"))
    assert saved["version"] == 1


def test_store_marks_inflight_tasks_interrupted_on_restart(tmp_path):
    kb = _kb(tmp_path)
    source = kb / "raw" / "waiting.md"
    source.write_text("# waiting", encoding="utf-8")
    store = FileTaskStore(kb)
    file_id = store.create_job(
        "job-1", kind="add", title="add: waiting", files=[(source, source.name)]
    )[0]

    restarted = FileTaskStore(kb)
    item = restarted.get_file(file_id)
    assert item is not None
    assert item["status"] == "interrupted"
    assert "restarted" in item["error"]
    assert restarted.list_jobs()[0]["status"] == "interrupted"


def test_store_reconciles_legacy_pending_and_compiled_sources(tmp_path):
    kb = _kb(tmp_path)
    (kb / "raw" / "done.md").write_text("# done", encoding="utf-8")
    (kb / "raw" / "pending.txt").write_text("pending", encoding="utf-8")
    (kb / ".openkb" / "hashes.json").write_text(
        json.dumps({"digest": {"name": "done.md", "doc_name": "done", "raw_path": "raw/done.md"}}),
        encoding="utf-8",
    )

    files = FileTaskStore(kb).list_files()
    assert {(item["name"], item["status"]) for item in files} == {
        ("done.md", "succeeded"),
        ("pending.txt", "pending"),
    }


def test_store_rejects_corrupt_json(tmp_path):
    kb = _kb(tmp_path)
    (kb / ".openkb" / "file-tasks.json").write_text("not json", encoding="utf-8")
    with pytest.raises(FileTaskStateError, match="Could not read"):
        FileTaskStore(kb)
