"""Legal graph tools for the OpenKB agent - typed-relation traversal + impact.

These sit above :mod:`openkb.legal.graph` (the storage) and
:mod:`openkb.legal.graph_extract` (the populator), exposing the two graph
queries that pure text retrieval cannot answer (spec section 3.7):

- :func:`query_graph` - traverse typed relations from an entity (e.g. "what
  statutes does this case CITES?"). The graph-traversal leg of the Query Router.
- :func:`find_impact` - reverse traversal: when a statute changes, what cases /
  concepts / rules reference it (the "影响面分析" that BM25+vector miss).

Both take ``kb_root`` (the KB root, containing ``.openkb/graph/``) and return
readable strings for the agent. ``build_legal_query_agent`` wires them into the
legal agent alongside the DocIR read/search/citation tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from openkb.legal.graph import LegalKnowledgeGraph
from openkb.legal.schema import RelationType


def _resolve_relation(relation: str) -> Optional[RelationType]:
    """Map a relation name (e.g. ``"cites"``) to a RelationType. None if unknown."""
    relation = relation.strip().lower()
    for rt in RelationType:
        if rt.value == relation:
            return rt
    return None


def _find_node(graph: LegalKnowledgeGraph, entity: str):
    """Find a graph node by label (case-insensitive) or id prefix."""
    node = graph.find_node(entity)
    if node is not None:
        return node
    # Fuzzy: case-insensitive label match across all nodes.
    target = entity.strip().lower()
    for n in graph._nodes.values():
        if n.label.lower() == target or target in n.label.lower():
            return n
    return None


def query_graph(entity: str, relation: str, kb_root: str, depth: int = 2) -> str:
    """Traverse typed relations outward from an entity.

    Args:
        entity: Entity label (e.g. ``"张某案"``) - resolved case-insensitively.
        relation: Relation type (e.g. ``"cites"``, ``"applies"``, ``"revises"``).
            See :class:`openkb.legal.schema.RelationType` for the full set.
        kb_root: Absolute path to the KB root.
        depth: Max traversal depth (default 2).

    Returns:
        Formatted traversal: ``depth N: <label> (<type>) [via <relation>]``,
        or a not-found / no-results message.
    """
    graph = LegalKnowledgeGraph(Path(kb_root).resolve())
    node = _find_node(graph, entity)
    if node is None:
        return f"Entity not found in graph: {entity}"
    rt = _resolve_relation(relation)
    if rt is None:
        return f"Unknown relation: {relation}. Known: " + ", ".join(rt.value for rt in RelationType)
    if depth <= 1:
        results = graph.find_related(node.node_id, rt)
        if not results:
            return f"No {relation} relations from {node.label}."
        lines = [
            f"depth 1: {n.label} ({n.node_type}) [{rt.value}, conf={e.confidence:.2f}]"
            for n, e in results
        ]
        return "\n".join(lines) + "\n"

    traversal = graph.traverse(node.node_id, relation_types=[rt], max_depth=depth)
    if not traversal:
        return f"No {relation} traversal from {node.label}."
    lines = [
        f"depth {r.depth}: {r.node.label} ({r.node.node_type}) "
        f"[path: {' -> '.join(p.split('/')[-1] for p in r.path)}, weight={r.total_weight:.2f}]"
        for r in traversal
        if r.depth > 0
    ]
    if not lines:
        return f"No {relation} relations from {node.label}."
    return "\n".join(lines) + "\n"


def find_impact(entity: str, kb_root: str) -> str:
    """Reverse traversal: what references this entity (impact analysis)?

    When a statute/case changes, this returns everything that cites/applies/
    follows it - the structural impact that pure text retrieval cannot capture.

    Args:
        entity: Entity label (e.g. ``"民法典第577条"``).
        kb_root: Absolute path to the KB root.

    Returns:
        Formatted impact list with depths, or a not-found message.
    """
    graph = LegalKnowledgeGraph(Path(kb_root).resolve())
    node = _find_node(graph, entity)
    if node is None:
        return f"Entity not found in graph: {entity}"
    affected = graph.find_affecting_nodes(node.node_id)
    if not affected:
        return f"No nodes are affected by changes to {node.label}."
    lines = [
        f"depth {r.depth}: {r.node.label} ({r.node.node_type}) "
        f"[via {' -> '.join(e.relation_type.value for e in r.edges) or 'direct'}]"
        for r in affected
    ]
    return f"Impact of changing {node.label} ({len(affected)} node(s)):\n" + "\n".join(lines) + "\n"


def list_graph_entities(kb_root: str, node_type: str = "") -> str:
    """List graph entities, optionally filtered by node_type. For exploration."""
    graph = LegalKnowledgeGraph(Path(kb_root).resolve())
    if node_type:
        nodes = graph.get_nodes_by_type(node_type)
    else:
        nodes = list(graph._nodes.values())
    if not nodes:
        return "Graph is empty."
    lines = [f"- {n.label} ({n.node_type}) [id={n.node_id[:20]}...]" for n in nodes[:50]]
    header = f"{len(nodes)} entity(ies)" + (f" of type {node_type}" if node_type else "")
    return header + "\n" + "\n".join(lines) + "\n"


__all__ = ["query_graph", "find_impact", "list_graph_entities"]
