"""Regression tests for the JSON-backed, file-first task store."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from openkb.file_tasks import FileTaskStateError, FileTaskStore
from openkb.jobs import JobRegistry


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def test_store_dedupes_same_content_sources(tmp_path):
    """A raw file whose content matches a registered source under a different
    name is a duplicate: the copy is deleted and no pending record is seeded."""
    kb = _kb(tmp_path)
    (kb / "raw" / "paper-dash.pdf").write_bytes(b"same bytes")
    (kb / "raw" / "paper.dot.pdf").write_bytes(b"same bytes")  # duplicate copy
    (kb / ".openkb" / "hashes.json").write_text(
        json.dumps(
            {
                _digest(b"same bytes"): {
                    "name": "paper-dash.pdf",
                    "doc_name": "paper",
                    "raw_path": "raw/paper-dash.pdf",
                }
            }
        ),
        encoding="utf-8",
    )

    files = FileTaskStore(kb).list_files()
    assert [(item["name"], item["status"]) for item in files] == [
        ("paper-dash.pdf", "succeeded")
    ]
    assert not (kb / "raw" / "paper.dot.pdf").exists()
    assert (kb / "raw" / "paper-dash.pdf").exists()


def test_store_dedupe_promotes_pending_to_succeeded(tmp_path):
    """The canonical registered file may itself hold only a pending record
    while the duplicate name carries the succeeded one (arXiv -/. split). The
    pending record is promoted and backfilled, and the duplicate's record and
    file are removed."""
    kb = _kb(tmp_path)
    (kb / "raw" / "2404-18759v2.pdf").write_bytes(b"the paper")
    (kb / "raw" / "2404.18759v2.pdf").write_bytes(b"the paper")  # duplicate copy
    (kb / ".openkb" / "hashes.json").write_text(
        json.dumps(
            {
                _digest(b"the paper"): {
                    "name": "2404.18759v2.pdf",
                    "doc_name": "2404-18759v2",
                    "raw_path": "raw/2404-18759v2.pdf",
                }
            }
        ),
        encoding="utf-8",
    )
    (kb / ".openkb" / "file-tasks.json").write_text(
        json.dumps(
            {
                "version": 1,
                "files": {
                    "dup": {
                        "id": "dup",
                        "name": "2404.18759v2.pdf",
                        "raw_path": "2404.18759v2.pdf",
                        "source_hash": None,
                        "document_name": None,
                        "status": "succeeded",
                        "step": "finalize",
                        "completed_steps": 4,
                        "total_steps": 4,
                        "message": None,
                        "error": None,
                        "created_by": "legacy",
                        "created_at": 1,
                        "updated_at": 1,
                        "last_job_id": None,
                        "history": [],
                    },
                    "keep": {
                        "id": "keep",
                        "name": "2404-18759v2.pdf",
                        "raw_path": "2404-18759v2.pdf",
                        "source_hash": None,
                        "document_name": None,
                        "status": "pending",
                        "step": "prepare",
                        "completed_steps": 0,
                        "total_steps": 4,
                        "message": None,
                        "error": None,
                        "created_by": "legacy",
                        "created_at": 0,
                        "updated_at": 0,
                        "last_job_id": None,
                        "history": [],
                    },
                },
                "jobs": {},
            }
        ),
        encoding="utf-8",
    )

    digest = _digest(b"the paper")
    files = FileTaskStore(kb).list_files()
    assert len(files) == 1
    kept = files[0]
    assert kept["name"] == "2404-18759v2.pdf"
    assert kept["status"] == "succeeded"
    assert kept["source_hash"] == digest
    assert kept["document_name"] == "2404-18759v2"
    assert not (kb / "raw" / "2404.18759v2.pdf").exists()
    # Registry normalized onto the kept file.
    hashes = json.loads((kb / ".openkb" / "hashes.json").read_text(encoding="utf-8"))
    assert hashes[digest]["raw_path"] == "raw/2404-18759v2.pdf"
    assert hashes[digest]["name"] == "2404-18759v2.pdf"


def test_store_dedupe_leaves_unregistered_duplicates_alone(tmp_path):
    """Same-content files whose hash is NOT in the registry are ambiguous -
    the pass must not delete them."""
    kb = _kb(tmp_path)
    (kb / "raw" / "a.md").write_text("x")
    (kb / "raw" / "b.md").write_text("x")
    (kb / ".openkb" / "hashes.json").write_text(
        json.dumps({"unrelated": {"name": "c.md", "raw_path": "raw/c.md"}}),
        encoding="utf-8",
    )

    files = FileTaskStore(kb).list_files()
    assert (kb / "raw" / "a.md").exists()
    assert (kb / "raw" / "b.md").exists()
    # Neither a.md nor b.md is registered; both get seeded as pending as before.
    # c.md is registered so reconcile seeds its succeeded record even though the
    # file is absent.
    assert {(item["name"], item["status"]) for item in files} == {
        ("a.md", "pending"),
        ("b.md", "pending"),
        ("c.md", "succeeded"),
    }


def test_store_rejects_corrupt_json(tmp_path):
    kb = _kb(tmp_path)
    (kb / ".openkb" / "file-tasks.json").write_text("not json", encoding="utf-8")
    with pytest.raises(FileTaskStateError, match="Could not read"):
        FileTaskStore(kb)
