"""Ingest event hooks - automate the "bookkeeping" (spec section 3.11).

Manual bookkeeping is the #1 reason wikis get abandoned. These hooks run the
post-ingest automation that keeps the KB consistent without human effort:

- :func:`run_ingest_hooks` - after a doc is compiled, (re)extract the legal
  knowledge graph from the wiki and annotate the doc's summary page with
  default lifecycle frontmatter. Intended as an ``AddMutationPlan``
  ``post_commit_hook`` (or callable from the sync apply path).
- :func:`run_maintenance_hooks` - the periodic self-heal sweep: re-extract the
  graph, list pages needing review. (A full cron is out of scope; this is the
  callable the scheduler would invoke.)

Both are idempotent and best-effort - a hook failure logs and continues rather
than failing the ingest (the mutation already committed).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from openkb.legal.graph_extract import extract_doc_graph, graph_stats
from openkb.legal.lifecycle_ops import annotate_doc_lifecycle, pages_needing_review

logger = logging.getLogger(__name__)


@dataclass
class HookResult:
    """Outcome of running ingest hooks for one doc."""

    doc_name: str
    graph_nodes: int = 0
    graph_edges: int = 0
    lifecycle_annotated: bool = False
    errors: List[str] = field(default_factory=list)


def run_ingest_hooks(kb_dir: Path | str, doc_name: str) -> HookResult:
    """Post-ingest automation: graph extraction + lifecycle annotation.

    Call this as a post-commit hook after a doc is compiled (or from the sync
    apply path). Best-effort: each step is guarded so one failing step doesn't
    skip the other, and a hook failure never fails the ingest (the mutation
    already committed).
    """
    kb = Path(kb_dir).resolve()
    result = HookResult(doc_name=doc_name)

    # 1. Re-extract the legal knowledge graph from the (now-updated) wiki.
    try:
        extract_doc_graph(kb, doc_name)
        stats = graph_stats(kb)
        result.graph_nodes = stats.get("node_count", 0)
        result.graph_edges = stats.get("edge_count", 0)
    except Exception as exc:  # noqa: BLE001 - best-effort hook
        result.errors.append(f"graph_extract_failed: {exc}")
        logger.warning("ingest hook graph_extract failed for %s: %s", doc_name, exc)

    # 2. Annotate the doc's summary page with default lifecycle frontmatter.
    try:
        result.lifecycle_annotated = annotate_doc_lifecycle(kb, doc_name)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"lifecycle_annotate_failed: {exc}")
        logger.warning("ingest hook lifecycle_annotate failed for %s: %s", doc_name, exc)

    return result


@dataclass
class MaintenanceResult:
    """Outcome of a periodic maintenance sweep."""

    graph_nodes: int = 0
    graph_edges: int = 0
    pages_needing_review: List[dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def run_maintenance_hooks(
    kb_dir: Path | str, *, confidence_threshold: float = 0.5
) -> MaintenanceResult:
    """Periodic self-heal sweep (spec section 3.12).

    Re-extracts the graph (picks up any manual edits) and surfaces pages
    needing review (active but low-confidence). A full cron/scheduler is out of
    scope; this is the callable a scheduler would invoke.
    """
    kb = Path(kb_dir).resolve()
    result = MaintenanceResult()
    try:
        extract_doc_graph(kb, "_maintenance_")
        stats = graph_stats(kb)
        result.graph_nodes = stats.get("node_count", 0)
        result.graph_edges = stats.get("edge_count", 0)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"graph_extract_failed: {exc}")
    try:
        result.pages_needing_review = pages_needing_review(kb, confidence_threshold)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"pages_needing_review_failed: {exc}")
    return result


__all__ = [
    "HookResult", "MaintenanceResult",
    "run_ingest_hooks", "run_maintenance_hooks", "make_ingest_hook",
]


def make_ingest_hook(kb_dir: Path | str, doc_name: str):
    """Build a no-arg post-commit hook for ``run_ingest_hooks``.

    Returns a closure usable as an ``AddMutationPlan.post_commit_hooks`` entry,
    so the spec's ingest automation (graph extract + lifecycle annotate) runs
    automatically after every ``openkb add`` without callers hand-wiring it.
    """
    kb = Path(kb_dir).resolve()

    def _hook() -> None:
        run_ingest_hooks(kb, doc_name)

    return _hook
