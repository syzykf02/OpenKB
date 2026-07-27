"""Legal REST endpoints - knowledge graph + lifecycle (UI_INTEGRATION_PLAN §7).

Split into its own APIRouter (like ``api_graph_router``) so ``api.py`` stays
under the per-file line limit. Read endpoints use ``run_in_threadpool`` to keep
the sync graph/lifecycle I/O off the event loop; write endpoints (supersede /
confirm) mutate through the lifecycle_ops layer (atomic frontmatter writes).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from openkb.api_helpers import _resolve_kb, require_bearer_token

legal_router = APIRouter()


# ----------------------------------------------------------------------------
# Request / response models
# ----------------------------------------------------------------------------
class SupersedeRequest(BaseModel):
    superseded_by: str
    reason: str
    triggered_by: str = "manual"


class ConfidenceRequest(BaseModel):
    new_confidence: Optional[float] = None
    add_source: bool = False
    decay_rate: Optional[str] = None


# ----------------------------------------------------------------------------
# Graph endpoints
# ----------------------------------------------------------------------------
@legal_router.get("/api/v1/legal/graph/nodes")
async def legal_graph_nodes(
    kb: str = Query(...),
    node_type: Optional[str] = Query(None),
    _: None = Depends(require_bearer_token),
) -> dict:
    """List graph nodes, optionally filtered by node_type."""
    kb_dir = _resolve_kb(kb)

    def _build() -> dict:
        from openkb.legal.graph import LegalKnowledgeGraph

        graph = LegalKnowledgeGraph(kb_dir)
        nodes = graph.get_nodes_by_type(node_type) if node_type else list(graph._nodes.values())
        return {
            "nodes": [n.to_dict() for n in nodes[:200]],
            "total": len(nodes),
        }

    return await run_in_threadpool(_build)


@legal_router.get("/api/v1/legal/graph/nodes/{node_id}")
async def legal_graph_node(
    node_id: str,
    kb: str = Query(...),
    _: None = Depends(require_bearer_token),
) -> dict:
    """Get a single graph node."""
    kb_dir = _resolve_kb(kb)

    def _get() -> dict:
        from openkb.legal.graph import LegalKnowledgeGraph

        node = LegalKnowledgeGraph(kb_dir).get_node(node_id)
        return node.to_dict() if node else {"error": "not found"}

    return await run_in_threadpool(_get)


@legal_router.get("/api/v1/legal/graph/nodes/{node_id}/related")
async def legal_graph_related(
    node_id: str,
    kb: str = Query(...),
    relation: Optional[str] = Query(None),
    depth: int = Query(2, ge=1, le=4),
    _: None = Depends(require_bearer_token),
) -> dict:
    """Nodes related to ``node_id`` via typed relations."""
    kb_dir = _resolve_kb(kb)

    def _related() -> dict:
        from openkb.legal.graph import LegalKnowledgeGraph
        from openkb.legal.schema import RelationType

        graph = LegalKnowledgeGraph(kb_dir)
        if relation:
            rt = next((r for r in RelationType if r.value == relation), None)
            if rt is None:
                return {"related": [], "error": f"unknown relation: {relation}"}
            results = graph.find_related(node_id, rt)
            return {"related": [{"node": n.to_dict(), "edge": e.to_dict()} for n, e in results]}
        traversal = graph.traverse(node_id, max_depth=depth)
        return {
            "related": [
                {"node": r.node.to_dict(), "depth": r.depth} for r in traversal if r.depth > 0
            ]
        }

    return await run_in_threadpool(_related)


@legal_router.get("/api/v1/legal/graph/nodes/{node_id}/impact")
async def legal_graph_impact(
    node_id: str,
    kb: str = Query(...),
    _: None = Depends(require_bearer_token),
) -> dict:
    """Impact analysis - what references this node (reverse traversal)."""
    kb_dir = _resolve_kb(kb)

    def _impact() -> dict:
        from openkb.legal.graph import LegalKnowledgeGraph

        affected = LegalKnowledgeGraph(kb_dir).find_affecting_nodes(node_id)
        return {
            "affected": [
                {
                    "node": r.node.to_dict(),
                    "depth": r.depth,
                    "via": [e.relation_type.value for e in r.edges],
                }
                for r in affected
            ],
            "summary": f"{len(affected)} node(s) affected",
        }

    return await run_in_threadpool(_impact)


@legal_router.get("/api/v1/legal/graph/contradictions")
async def legal_graph_contradictions(
    kb: str = Query(...),
    _: None = Depends(require_bearer_token),
) -> dict:
    """Detect contradiction edges in the graph."""
    kb_dir = _resolve_kb(kb)

    def _contradictions() -> dict:
        from openkb.legal.graph import LegalKnowledgeGraph

        pairs = LegalKnowledgeGraph(kb_dir).detect_contradictions()
        return {
            "contradictions": [
                {
                    "node1": n1.to_dict(),
                    "node2": n2.to_dict(),
                    "edges": [e.to_dict() for e in edges],
                }
                for n1, n2, edges in pairs
            ]
        }

    return await run_in_threadpool(_contradictions)


@legal_router.get("/api/v1/legal/graph")
async def legal_graph_full(
    kb: str = Query(...),
    _: None = Depends(require_bearer_token),
) -> dict:
    """Return the whole legal graph (nodes + edges) for visualization."""
    kb_dir = _resolve_kb(kb)

    def _full() -> dict:
        from openkb.legal.graph import LegalKnowledgeGraph

        graph = LegalKnowledgeGraph(kb_dir)
        return {
            "nodes": [n.to_dict() for n in graph._nodes.values()],
            "edges": [e.to_dict() for e in graph._edges.values()],
        }

    return await run_in_threadpool(_full)


@legal_router.get("/api/v1/legal/docir/by-hash/{doc_hash}")
async def legal_docir_by_hash(
    doc_hash: str,
    kb: str = Query(...),
    _: None = Depends(require_bearer_token),
) -> dict:
    """Resolve a document by content hash and return its DocIR (tree + visuals).

    The reader opens documents by content hash; DocIR is stored under the
    collision-resistant ``doc_name``. The hash registry maps hash -> doc_name,
    so this endpoint bridges the two for the reader's DocIR structure panel.
    Returns ``{"docir": null, "doc_name": null}`` when no DocIR exists for the
    document (e.g. pre-DocIR ingest) - the frontend hides the panel then.
    """
    kb_dir = _resolve_kb(kb)

    def _load() -> dict:
        from openkb.docir import DocIRDocument
        from openkb.state import HashRegistry

        reg = HashRegistry(kb_dir / ".openkb" / "hashes.json")
        meta = reg.get(doc_hash) or {}
        doc_name = meta.get("doc_name")
        if not doc_name:
            return {"docir": None, "doc_name": None}
        path = kb_dir / "wiki" / "sources" / f"{doc_name}.docir.json"
        if not path.exists():
            return {"docir": None, "doc_name": doc_name}
        return {"docir": DocIRDocument.load(path).to_dict(), "doc_name": doc_name}

    return await run_in_threadpool(_load)


# ----------------------------------------------------------------------------
# Lifecycle endpoints
# ----------------------------------------------------------------------------
@legal_router.get("/api/v1/legal/lifecycle")
async def legal_lifecycle_list(
    kb: str = Query(...),
    status: Optional[str] = Query(None),
    _: None = Depends(require_bearer_token),
) -> dict:
    """List pages with their lifecycle state."""
    kb_dir = _resolve_kb(kb)

    def _list() -> dict:
        from openkb.legal.lifecycle_ops import list_lifecycle_pages

        pages = list_lifecycle_pages(kb_dir)
        if status:
            pages = [p for p in pages if p["status"] == status]
        return {"pages": pages, "total": len(pages)}

    return await run_in_threadpool(_list)


@legal_router.get("/api/v1/legal/lifecycle/{page_path:path}")
async def legal_lifecycle_show(
    page_path: str,
    kb: str = Query(...),
    _: None = Depends(require_bearer_token),
) -> dict:
    """Show one page's lifecycle."""
    kb_dir = _resolve_kb(kb)

    def _show() -> dict:
        from openkb.legal.lifecycle_ops import read_page_lifecycle

        lc = read_page_lifecycle(kb_dir, page_path)
        return {
            "page_path": lc.page_path,
            "version": lc.version,
            "status": lc.supersede.status.value if lc.supersede.status else "active",
            "confidence": lc.confidence.confidence,
            "sources_count": lc.confidence.sources_count,
            "decay_rate": lc.confidence.decay_rate.value,
            "superseded_by": lc.supersede.superseded_by,
            "supersede_reason": lc.supersede.supersede_reason,
            "history": lc.confidence.confirmed_history,
        }

    return await run_in_threadpool(_show)


@legal_router.patch("/api/v1/legal/lifecycle/{page_path:path}/confidence")
async def legal_lifecycle_confidence(
    page_path: str,
    request: ConfidenceRequest,
    kb: str = Query(...),
    _: None = Depends(require_bearer_token),
) -> dict:
    """Update a page's confidence (confirm / add source)."""
    kb_dir = _resolve_kb(kb)

    def _update() -> dict:
        from openkb.legal.lifecycle_ops import confirm_page
        from openkb.legal.schema import DecayRate

        decay = DecayRate(request.decay_rate) if request.decay_rate else None
        lc = confirm_page(
            kb_dir,
            page_path,
            confidence=request.new_confidence,
            add_source=request.add_source,
            decay_rate=decay,
        )
        return {
            "confidence": lc.confidence.confidence,
            "sources_count": lc.confidence.sources_count,
            "version": lc.version,
        }

    return await run_in_threadpool(_update)


@legal_router.post("/api/v1/legal/lifecycle/{page_path:path}/supersede")
async def legal_lifecycle_supersede(
    page_path: str,
    request: SupersedeRequest,
    kb: str = Query(...),
    _: None = Depends(require_bearer_token),
) -> dict:
    """Mark a page as superseded."""
    kb_dir = _resolve_kb(kb)

    def _supersede() -> dict:
        from openkb.legal.lifecycle_ops import supersede_page

        lc = supersede_page(
            kb_dir, page_path, request.superseded_by, request.reason, request.triggered_by
        )
        return {
            "status": lc.supersede.status.value,
            "superseded_by": lc.supersede.superseded_by,
            "version": lc.version,
        }

    return await run_in_threadpool(_supersede)


__all__ = ["legal_router"]
