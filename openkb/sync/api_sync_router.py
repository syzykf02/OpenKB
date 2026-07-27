"""Sync REST endpoints (UI_INTEGRATION_PLAN §7.2)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from openkb.api_helpers import _resolve_kb, require_bearer_token

sync_router = APIRouter()


class AddSourceRequest(BaseModel):
    source_id: str
    path: str
    name: Optional[str] = None
    auto_sync: bool = False
    sync_interval_minutes: int = 60
    include_patterns: list[str] = []
    exclude_patterns: list[str] = []


@sync_router.get("/api/v1/legal/sync/sources")
async def sync_list_sources(
    kb: str = Query(...),
    _: None = Depends(require_bearer_token),
) -> dict:
    kb_dir = _resolve_kb(kb)
    return await run_in_threadpool(lambda: _sources(kb_dir))


def _sources(kb_dir) -> dict:
    from openkb.sync import SyncEngine

    return SyncEngine(kb_dir).stats()


@sync_router.post("/api/v1/legal/sync/sources")
async def sync_add_source(
    request: AddSourceRequest,
    kb: str = Query(...),
    _: None = Depends(require_bearer_token),
) -> dict:
    kb_dir = _resolve_kb(kb)

    def _add() -> dict:
        from openkb.sync import SyncEngine, SyncSourceType

        engine = SyncEngine(kb_dir)
        source = engine.register_source(
            request.source_id,
            SyncSourceType.LOCAL_DIR,
            path=request.path,
            name=request.name,
            auto_sync=request.auto_sync,
            sync_interval_minutes=request.sync_interval_minutes,
            include_patterns=request.include_patterns,
            exclude_patterns=request.exclude_patterns,
        )
        return {"source_id": source.source_id, "name": source.name, "path": source.path}

    return await run_in_threadpool(_add)


@sync_router.post("/api/v1/legal/sync/sources/{source_id}/scan")
async def sync_scan_source(
    source_id: str,
    kb: str = Query(...),
    _: None = Depends(require_bearer_token),
) -> dict:
    kb_dir = _resolve_kb(kb)

    def _scan() -> dict:
        from openkb.sync import SyncEngine

        engine = SyncEngine(kb_dir)
        try:
            _source, entries, diff = engine.scan_source(source_id)
        except ValueError as exc:
            return {"error": str(exc)}
        return {
            "new_files": diff.new_files,
            "modified_files": diff.modified_files,
            "deleted_files": diff.deleted_files,
            "unchanged": len(diff.unchanged_files),
            "total_scanned": len(entries),
        }

    return await run_in_threadpool(_scan)


@sync_router.post("/api/v1/legal/sync/sources/{source_id}/sync")
async def sync_apply_source(
    source_id: str,
    kb: str = Query(...),
    full: bool = Query(False),
    _: None = Depends(require_bearer_token),
) -> dict:
    """Apply a source's diff - ingest new/modified files."""
    kb_dir = _resolve_kb(kb)

    def _apply() -> dict:
        from openkb.sync import SyncEngine
        from openkb.sync.sync_cli import _make_full_ingest_callback

        engine = SyncEngine(kb_dir)
        callback = _make_full_ingest_callback(kb_dir) if full else None
        try:
            result = engine.apply_diff(source_id, ingest_callback=callback)
        except ValueError as exc:
            return {"error": str(exc)}
        return {
            "ingested": [{"path": p, "outcome": o} for p, o in result.ingested],
            "deleted": result.deleted,
            "errors": result.errors,
            "total_changed": result.total_changed,
        }

    return await run_in_threadpool(_apply)


__all__ = ["sync_router"]
