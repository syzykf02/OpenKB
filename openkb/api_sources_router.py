"""REST endpoints for locally stored source files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from openkb.api_helpers import _resolve_kb, require_bearer_token
from openkb.api_ingest import start_add_job
from openkb.api_models import CompilePendingDocumentRequest, KbRequest, ListResponse
from openkb.cli import SUPPORTED_EXTENSIONS, get_kb_list
from openkb.config import resolve_credential_bundle
from openkb.locks import kb_ingest_lock

sources_router = APIRouter()


def _delete_pending_raw_source(kb_dir: Path, raw_name: str) -> bool:
    """Delete a source only while it remains uncompiled and pending."""
    with kb_ingest_lock(kb_dir / ".openkb"):
        pending_names = {item["path"] for item in get_kb_list(kb_dir)["pending_documents"]}
        if raw_name not in pending_names:
            return False
        (kb_dir / "raw" / raw_name).unlink()
        return True


@sources_router.post("/api/v1/list", response_model=ListResponse)
async def list_sources_endpoint(
    request: KbRequest,
    _: None = Depends(require_bearer_token),
) -> ListResponse:
    """Return compiled and pending source inventory for a knowledge base."""
    kb_dir = _resolve_kb(request.kb)
    try:
        return ListResponse(**await run_in_threadpool(get_kb_list, kb_dir))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"List failed: {exc}") from exc


@sources_router.post("/api/v1/documents/compile")
async def compile_pending_document_endpoint(
    request: CompilePendingDocumentRequest,
    fastapi_request: Request,
    _: None = Depends(require_bearer_token),
) -> dict[str, Any]:
    """Resume compilation of one raw source that is already on disk."""
    kb_dir = _resolve_kb(request.kb)
    raw_name = Path(request.path).name
    if raw_name != request.path:
        raise HTTPException(status_code=400, detail="Invalid raw source path.")
    raw_path = kb_dir / "raw" / raw_name
    if not raw_path.is_file():
        raise HTTPException(status_code=404, detail="Uploaded source not found.")
    if raw_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported source type.")
    job = start_add_job(
        fastapi_request.app.state.jobs_registry,
        request.kb,
        kb_dir,
        [(raw_path, raw_name)],
        bundle=resolve_credential_bundle(kb_dir),
        store=fastapi_request.app.state.file_task_stores.for_kb(kb_dir),
    )
    return {"job_id": job.id, "kb": request.kb, "status": job.status, "file_ids": job.file_ids}


@sources_router.post("/api/v1/documents/pending/delete")
async def delete_pending_document_endpoint(
    request: CompilePendingDocumentRequest,
    _: None = Depends(require_bearer_token),
) -> dict[str, str]:
    """Remove one uploaded source that has not been compiled yet."""
    kb_dir = _resolve_kb(request.kb)
    raw_name = Path(request.path).name
    if raw_name != request.path:
        raise HTTPException(status_code=400, detail="Invalid raw source path.")
    deleted = await run_in_threadpool(_delete_pending_raw_source, kb_dir, raw_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Pending uploaded source not found.")
    return {"status": "deleted", "name": raw_name}
