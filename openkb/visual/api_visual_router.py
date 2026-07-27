"""Visual analysis REST endpoints (UI_INTEGRATION_PLAN §7.4)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from openkb.api_helpers import _resolve_kb, require_bearer_token

visual_router = APIRouter()


class AnalyzeRequest(BaseModel):
    question: str
    model: Optional[str] = None


@visual_router.get("/api/v1/legal/visual/{doc_name}/page/{page_number}")
async def visual_nodes_for_page(
    doc_name: str,
    page_number: int,
    kb: str = Query(...),
    _: None = Depends(require_bearer_token),
) -> dict:
    """List visual nodes registered on a page."""
    kb_dir = _resolve_kb(kb)

    def _nodes() -> dict:
        from openkb.agent.docir_tools import _load_docir

        doc = _load_docir(doc_name, kb_dir / "wiki")
        if doc is None:
            return {"nodes": [], "error": "docir not found"}
        nodes = [n for n in doc.get_nodes_by_page(page_number) if n.is_visual()]
        return {
            "nodes": [
                {
                    "id": n.id,
                    "type": n.vision.type if n.vision else "unknown",
                    "text_anchor": n.vision.text_anchor if n.vision else None,
                    "render_ref": n.vision.render_ref if n.vision else None,
                    "analyzed": n.vision.analyzed if n.vision else False,
                }
                for n in nodes
            ]
        }

    return await run_in_threadpool(_nodes)


@visual_router.post("/api/v1/legal/visual/{node_id}/analyze")
async def visual_analyze_node(
    node_id: str,
    request: AnalyzeRequest,
    kb: str = Query(...),
    _: None = Depends(require_bearer_token),
) -> dict:
    """On-demand vision analysis of a node (text-first gate; caches result)."""
    kb_dir = _resolve_kb(kb)

    def _analyze() -> dict:
        from openkb.visual.analyzer import VisionAnalyzer

        analyzer = VisionAnalyzer(kb_dir, model=request.model or "gpt-4o")
        result = analyzer.analyze_node(node_id, request.question)
        return result.to_dict()

    return await run_in_threadpool(_analyze)


@visual_router.post("/api/v1/legal/visual/page/analyze")
async def visual_analyze_page(
    request: AnalyzeRequest,
    doc_name: str = Query(...),
    page: str = Query(...),
    kb: str = Query(...),
    _: None = Depends(require_bearer_token),
) -> dict:
    """On-demand vision analysis of a whole page (text-first gate)."""
    kb_dir = _resolve_kb(kb)

    def _analyze() -> dict:
        from openkb.visual.analyzer import VisionAnalyzer

        analyzer = VisionAnalyzer(kb_dir, model=request.model or "gpt-4o")
        result = analyzer.analyze_page(doc_name, page, request.question)
        return result.to_dict()

    return await run_in_threadpool(_analyze)


__all__ = ["visual_router"]
