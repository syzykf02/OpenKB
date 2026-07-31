"""Persistent, file-first compilation state for the REST API.

Each knowledge base owns ``.openkb/file-tasks.json``.  The API keeps one
instance of :class:`FileTaskStore` in memory per KB, while every state change
is atomically written back to disk so a refresh or server restart retains the
source's latest outcome and its task/log history.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from openkb.locks import atomic_write_json, flock, funlock
from openkb.state import HashRegistry

STATE_VERSION = 1
ACTIVE_FILE_STATUSES = frozenset({"queued", "running"})
TERMINAL_FILE_STATUSES = frozenset(
    {"succeeded", "skipped", "failed", "cancelled", "interrupted", "deleted", "pending"}
)
MAX_LOGS_PER_TASK = 500


class FileTaskStateError(RuntimeError):
    """Raised when a persisted file-task state file cannot be safely read."""


def _now() -> float:
    return time.time()


def _empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "files": {}, "jobs": {}}


class FileTaskStore:
    """In-memory view and atomic JSON persistence for one knowledge base."""

    def __init__(self, kb_dir: Path) -> None:
        self.kb_dir = kb_dir
        self.path = kb_dir / ".openkb" / "file-tasks.json"
        self.lock_path = kb_dir / ".openkb" / "file-tasks.lock"
        self._lock = threading.RLock()
        self._state = self._load()
        self._mark_interrupted_after_restart()
        self._dedupe_duplicate_sources()
        self.reconcile_sources()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_state()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FileTaskStateError(f"Could not read {self.path.name}: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
            raise FileTaskStateError(
                f"Unsupported {self.path.name} version; expected {STATE_VERSION}."
            )
        files = raw.get("files")
        jobs = raw.get("jobs")
        if not isinstance(files, dict) or not isinstance(jobs, dict):
            raise FileTaskStateError(f"Invalid {self.path.name} shape.")
        return {"version": STATE_VERSION, "files": files, "jobs": jobs}

    @contextlib.contextmanager
    def _mutate(self) -> Iterator[None]:
        with self._lock:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock_path.open("a+", encoding="utf-8") as handle:
                flock(handle, exclusive=True)
                try:
                    yield
                    atomic_write_json(self.path, self._state)
                finally:
                    funlock(handle)

    def _mark_interrupted_after_restart(self) -> None:
        changed = False
        now = _now()
        for job in self._state["jobs"].values():
            if job.get("status") in {"queued", "running"}:
                job.update(
                    {
                        "status": "interrupted",
                        "finished_at": now,
                        "error": "Server restarted before this task completed.",
                    }
                )
                changed = True
        for file in self._state["files"].values():
            if file.get("status") in ACTIVE_FILE_STATUSES:
                file.update(
                    {
                        "status": "interrupted",
                        "error": "Server restarted before this file completed.",
                        "updated_at": now,
                    }
                )
                changed = True
        if changed:
            with self._mutate():
                pass

    def _dedupe_duplicate_sources(self) -> None:
        """Collapse same-content raw files into one source, file + records.

        ``reconcile_sources`` matches raw files to the hashes registry by
        FILENAME, so an upload whose name differs from the registered raw path
        (e.g. an arXiv ``2605.15184v1.pdf`` vs the registered
        ``2605-15184v1.pdf``) gets seeded as a fresh ``pending`` source even
        though its bytes are already in the KB. This pass runs once per store
        instantiation (before reconcile, so reconcile never re-seeds): it hashes
        only raw files whose basename is NOT a registered raw path, and for each
        content hash already in the registry it keeps the registered file,
        deletes the duplicate copies and their task records, promotes/backfills
        the surviving file's record to ``succeeded``, and points the registry
        at the kept file. After a clean pass the unregistered-name set is empty,
        so restarts hash nothing.
        """
        raw_dir = self.kb_dir / "raw"
        if not raw_dir.exists():
            return
        hashes_path = self.kb_dir / ".openkb" / "hashes.json"
        try:
            hashes = (
                json.loads(hashes_path.read_text(encoding="utf-8")) if hashes_path.exists() else {}
            )
        except (OSError, json.JSONDecodeError):
            hashes = {}
        if not isinstance(hashes, dict) or not hashes:
            return

        registered_raw_names = {
            Path(str(meta.get("raw_path") or meta.get("path") or meta.get("name") or "")).name
            for meta in hashes.values()
            if isinstance(meta, dict)
        }
        registered_raw_names.discard("")
        candidates = [
            path
            for path in raw_dir.iterdir()
            if path.is_file() and path.name not in registered_raw_names
        ]
        if not candidates:
            return

        groups: dict[str, list[Path]] = {}
        for path in candidates:
            try:
                digest = HashRegistry.hash_file(path)
            except OSError:
                continue
            groups.setdefault(digest, []).append(path)

        registry_changed = False
        with self._mutate():
            for digest, paths in groups.items():
                meta = hashes.get(digest)
                if not isinstance(meta, dict):
                    # Same-content files with no registry entry are ambiguous;
                    # never guess which to delete.
                    continue
                raw_ref = meta.get("raw_path") or meta.get("path") or meta.get("name")
                canonical_name = Path(str(raw_ref)).name if raw_ref else None
                canonical = (raw_dir / canonical_name) if canonical_name else None
                kept = None
                if canonical is not None and canonical.is_file():
                    # Only adopt the registered file when its bytes really match
                    # the duplicated content; a replaced same-named file (bytes
                    # differ) must not swallow the content-bearing candidates.
                    try:
                        if HashRegistry.hash_file(canonical) == digest:
                            kept = canonical
                    except OSError:
                        pass
                if kept is None:
                    kept = next(
                        (
                            p
                            for p in paths
                            if self._task_for_raw(p.name, include_deleted=False) is not None
                        ),
                        paths[0],
                    )
                for path in paths:
                    if path == kept:
                        continue
                    self._remove_raw_task_records(path.name)
                    try:
                        path.unlink()
                    except OSError:
                        pass
                new_raw = f"raw/{kept.name}"
                if (
                    meta.get("raw_path") != new_raw
                    or meta.get("path") != new_raw
                    or meta.get("name") != kept.name
                ):
                    meta = dict(meta)
                    meta["raw_path"] = new_raw
                    meta["path"] = new_raw
                    meta["name"] = kept.name
                    hashes[digest] = meta
                    registry_changed = True
                self._ensure_succeeded_record_locked(kept.name, digest, meta)
            if registry_changed:
                # Rewrite the registry snapshot: ``hashes[digest] = meta`` kept
                # the in-memory copy in sync with the normalized raw path/name.
                atomic_write_json(hashes_path, hashes)
            # ``_mutate`` writes ``self._state`` (file-tasks.json) on exit.

    def _task_for_raw(
        self, raw_name: str, *, include_deleted: bool
    ) -> dict[str, Any] | None:
        items = self._state["files"].values()
        if not include_deleted:
            items = (item for item in items if item.get("status") != "deleted")
        return next(
            (item for item in items if item.get("raw_path") == raw_name), None
        )

    def _remove_raw_task_records(self, raw_name: str) -> None:
        for file_id in [
            fid
            for fid, item in self._state["files"].items()
            if item.get("raw_path") == raw_name
        ]:
            del self._state["files"][file_id]

    def _ensure_succeeded_record_locked(
        self, raw_name: str, digest: str, meta: dict[str, Any]
    ) -> None:
        """Leave exactly one non-deleted ``succeeded`` task for ``raw_name``.

        Promotes an existing ``pending`` record (the file was on disk but never
        compiled to a task state) and backfills missing ``source_hash`` /
        ``document_name`` so the record resolves to the registered document.
        """
        items = [
            item
            for item in self._state["files"].values()
            if item.get("raw_path") == raw_name and item.get("status") != "deleted"
        ]
        doc_name = meta.get("doc_name")
        if not items:
            self._create_file_locked(
                name=raw_name,
                raw_path=raw_name,
                status="succeeded",
                source_hash=digest,
                document_name=doc_name,
                created_by="legacy",
            )
            return
        primary = items[0]
        primary.update(
            {
                "status": "succeeded",
                "step": "finalize",
                "completed_steps": 4,
                "total_steps": 4,
                "error": None,
            }
        )
        if not primary.get("source_hash"):
            primary["source_hash"] = digest
        if doc_name and not primary.get("document_name"):
            primary["document_name"] = doc_name
        for item in items[1:]:
            del self._state["files"][item["id"]]

    def reconcile_sources(self) -> None:
        """Seed legacy compiled/pending sources without overwriting task state."""
        raw_dir = self.kb_dir / "raw"
        hashes_path = self.kb_dir / ".openkb" / "hashes.json"
        try:
            hashes = (
                json.loads(hashes_path.read_text(encoding="utf-8")) if hashes_path.exists() else {}
            )
        except (OSError, json.JSONDecodeError):
            hashes = {}
        if not isinstance(hashes, dict):
            hashes = {}
        registered: dict[str, tuple[str, str]] = {}
        for digest, meta in hashes.items():
            if not isinstance(meta, dict):
                continue
            raw_ref = str(meta.get("raw_path") or meta.get("path") or meta.get("name") or "")
            raw_name = Path(raw_ref).name
            if raw_name:
                registered[raw_name] = (str(digest), str(meta.get("doc_name") or raw_name))
        changed = False
        with self._mutate():
            known_paths = {
                str(item.get("raw_path")): item
                for item in self._state["files"].values()
                if item.get("raw_path") and item.get("status") != "deleted"
            }
            for raw_name, (digest, document_name) in registered.items():
                if raw_name in known_paths:
                    item = known_paths[raw_name]
                    if not item.get("document_name"):
                        item["document_name"] = document_name
                        changed = True
                    continue
                self._create_file_locked(
                    name=raw_name,
                    raw_path=raw_name,
                    status="succeeded",
                    source_hash=digest,
                    document_name=document_name,
                    created_by="legacy",
                )
                changed = True
            if raw_dir.exists():
                registered_digests = set(hashes)
                for path in raw_dir.iterdir():
                    if not path.is_file() or path.name in registered or path.name in known_paths:
                        continue
                    # A file whose content hash is already registered under a
                    # different name is a duplicate source - delete it instead
                    # of seeding a fresh pending record. Only reached for names
                    # with no task/registry entry, so the polling path stays
                    # hash-free.
                    try:
                        if HashRegistry.hash_file(path) in registered_digests:
                            path.unlink()
                            continue
                    except OSError:
                        pass
                    self._create_file_locked(
                        name=path.name,
                        raw_path=path.name,
                        status="pending",
                        created_by="legacy",
                    )
                    changed = True
            if not changed:
                # ``_mutate`` writes on exit; avoid an unnecessary second write
                # only by retaining the straightforward locking path.
                return

    def _create_file_locked(
        self,
        *,
        name: str,
        raw_path: str,
        status: str,
        source_hash: str | None = None,
        document_name: str | None = None,
        created_by: str,
    ) -> str:
        file_id = uuid.uuid4().hex
        now = _now()
        self._state["files"][file_id] = {
            "id": file_id,
            "name": name,
            "raw_path": raw_path,
            "source_hash": source_hash,
            "document_name": document_name,
            "status": status,
            "step": "prepare" if status in {"queued", "running", "pending"} else "finalize",
            "completed_steps": 0 if status in {"queued", "running"} else 4,
            "total_steps": 4,
            "message": None,
            "error": None,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
            "last_job_id": None,
            "history": [],
        }
        return file_id

    def create_job(
        self,
        job_id: str,
        *,
        kind: str,
        title: str,
        files: list[tuple[Path, str]],
        existing_file_ids: list[str] | None = None,
    ) -> list[str]:
        """Persist a queued task and return its file ids in upload order."""
        with self._mutate():
            now = _now()
            file_ids = list(existing_file_ids or [])
            if not file_ids:
                for path, name in files:
                    file_ids.append(
                        self._create_file_locked(
                            name=name,
                            raw_path=path.name,
                            status="queued",
                            created_by=kind,
                        )
                    )
            for file_id in file_ids:
                item = self._state["files"].get(file_id)
                if item is None:
                    raise FileTaskStateError(f"Unknown file task: {file_id}")
                item.update(
                    {
                        "status": "queued",
                        "step": "prepare",
                        "completed_steps": 0,
                        "total_steps": 4,
                        "message": None,
                        "error": None,
                        "updated_at": now,
                        "last_job_id": job_id,
                    }
                )
                item.setdefault("history", []).append(
                    {
                        "job_id": job_id,
                        "kind": kind,
                        "status": "queued",
                        "created_at": now,
                        "logs": [],
                    }
                )
            self._state["jobs"][job_id] = {
                "id": job_id,
                "kind": kind,
                "title": title,
                "status": "queued",
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error": None,
                "file_ids": file_ids,
            }
            return file_ids

    def update_job(self, job) -> None:
        with self._mutate():
            item = self._state["jobs"].get(job.id)
            if item is None:
                return
            item.update(
                {
                    "status": job.status,
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                    "result": job.result,
                    "error": job.error,
                }
            )

    def record_event(self, job, event: str, data: dict[str, Any]) -> None:
        with self._mutate():
            job_state = self._state["jobs"].get(job.id)
            if job_state is None:
                return
            index = data.get("file_index")
            file_id = (
                job.file_ids[index]
                if isinstance(index, int) and index < len(job.file_ids)
                else None
            )
            if event == "log":
                for target_id in [file_id] if file_id else job.file_ids:
                    self._append_log_locked(target_id, job.id, data)
            elif file_id:
                self._apply_file_event_locked(file_id, job.id, event, data)
            if event == "cancelled":
                self._finish_active_files_locked(
                    job.file_ids, job.id, "cancelled", data.get("message")
                )
            elif event == "error":
                self._finish_active_files_locked(
                    job.file_ids, job.id, "failed", data.get("message")
                )

    def _append_log_locked(self, file_id: str, job_id: str, data: dict[str, Any]) -> None:
        item = self._state["files"].get(file_id)
        if item is None:
            return
        history = next(
            (h for h in reversed(item.get("history", [])) if h.get("job_id") == job_id), None
        )
        if history is None:
            return
        logs = history.setdefault("logs", [])
        logs.append(
            {"at": _now(), "level": data.get("level", "info"), "message": data.get("message", "")}
        )
        del logs[:-MAX_LOGS_PER_TASK]

    def _apply_file_event_locked(
        self, file_id: str, job_id: str, event: str, data: dict[str, Any]
    ) -> None:
        item = self._state["files"].get(file_id)
        if item is None:
            return
        now = _now()
        update: dict[str, Any] = {"updated_at": now}
        if event == "uploaded":
            update.update(
                {
                    "raw_path": Path(str(data.get("saved_path") or item["raw_path"])).name,
                    "status": "pending",
                }
            )
        elif event == "file_start":
            update.update(
                {
                    "status": "running",
                    "step": data.get("step", "prepare"),
                    "completed_steps": data.get("completed_steps", 0),
                }
            )
        elif event == "file_progress":
            update.update(
                {
                    "status": "running",
                    "step": data.get("step", "compile"),
                    "completed_steps": data.get("completed_steps", 0),
                    "total_steps": data.get("total_steps", 4),
                    "message": data.get("message"),
                }
            )
        elif event == "file_done":
            source_status = str(data.get("status", "failed"))
            status = {"added": "succeeded", "skipped": "skipped"}.get(source_status, source_status)
            update.update(
                {
                    "status": status,
                    "step": data.get("step", "finalize"),
                    "completed_steps": data.get(
                        "completed_steps", 4 if status in {"succeeded", "skipped"} else 2
                    ),
                    "total_steps": data.get("total_steps", 4),
                    "message": data.get("message"),
                    "error": data.get("message") if status == "failed" else None,
                }
            )
        item.update(update)
        history = next(
            (h for h in reversed(item.get("history", [])) if h.get("job_id") == job_id), None
        )
        if history is not None:
            history["status"] = item["status"]
            history["updated_at"] = now

    def _finish_active_files_locked(
        self, file_ids: list[str], job_id: str, status: str, message: str | None
    ) -> None:
        for file_id in file_ids:
            item = self._state["files"].get(file_id)
            if item is None or item.get("status") not in ACTIVE_FILE_STATUSES | {"pending"}:
                continue
            item.update({"status": status, "error": message, "updated_at": _now()})
            history = next(
                (h for h in reversed(item.get("history", [])) if h.get("job_id") == job_id), None
            )
            if history is not None:
                history["status"] = status

    def list_files(
        self, *, include_deleted: bool = False, status: str | None = None
    ) -> list[dict[str, Any]]:
        self.reconcile_sources()
        with self._lock:
            files = list(self._state["files"].values())
            if not include_deleted:
                files = [item for item in files if item.get("status") != "deleted"]
            if status:
                files = [item for item in files if item.get("status") == status]
            return sorted(
                (self._public_file(item) for item in files),
                key=lambda item: item["updated_at"],
                reverse=True,
            )

    def get_file(self, file_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._state["files"].get(file_id)
            return self._public_file(item) if item else None

    def list_jobs(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._state["jobs"].values())
            if active_only:
                jobs = [item for item in jobs if item.get("status") in {"queued", "running"}]
            return sorted((dict(item) for item in jobs), key=lambda item: item["created_at"])

    def mark_deleted(self, file_id: str) -> None:
        with self._mutate():
            item = self._state["files"].get(file_id)
            if item is None:
                raise FileTaskStateError(f"Unknown file task: {file_id}")
            item.update({"status": "deleted", "updated_at": _now(), "error": None})

    @staticmethod
    def _public_file(item: dict[str, Any]) -> dict[str, Any]:
        copy = dict(item)
        status = str(copy.get("status"))
        raw_path = copy.get("raw_path")
        actions: list[str] = []
        if status in {"queued", "running"}:
            actions.append("cancel")
        if (
            status in {"failed", "cancelled", "interrupted", "pending", "succeeded", "skipped"}
            and raw_path
        ):
            actions.append("compile")
        if status != "deleted":
            actions.append("delete")
        copy["actions"] = actions
        return copy


class FileTaskStores:
    """Application-scoped cache of per-KB :class:`FileTaskStore` instances."""

    def __init__(self) -> None:
        self._stores: dict[Path, FileTaskStore] = {}
        self._lock = threading.Lock()

    def for_kb(self, kb_dir: Path) -> FileTaskStore:
        key = kb_dir.resolve()
        with self._lock:
            store = self._stores.get(key)
            if store is None:
                store = FileTaskStore(key)
                self._stores[key] = store
            return store
