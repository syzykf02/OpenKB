"""Heuristic knowledge-graph extraction from compiled wiki pages.

Populates :class:`openkb.legal.graph.LegalKnowledgeGraph` from the wiki without
an LLM: each entity/concept page becomes a graph node (``node_type`` from the
page's ``type:`` frontmatter), and each ``[[wikilink]]`` becomes a typed edge
(``CITES`` by default - a page referencing another). This gives the graph
enough structure for :func:`query_graph` / :func:`find_impact` to work end to
end; the LLM-driven typed-relation extraction (``REVISES`` / ``APPLIES`` /
``CONTRADICTS`` etc. inferred from context) is Phase 3.

Idempotent: re-running upserts (``find_or_create_node`` / ``add_edge`` both
dedupe), so this is safe as a post-compile hook.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from openkb.frontmatter import parse as parse_frontmatter
from openkb.frontmatter import split as split_frontmatter
from openkb.legal.graph import LegalKnowledgeGraph
from openkb.legal.schema import LEGAL_ENTITY_TYPES, AuthorityLevel, RelationType

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# Capitalized entity type (compiler frontmatter) -> graph node_type (lowercase).
# Falls back to the lowercased frontmatter type, then "other".
_PAGE_TYPE_TO_NODE_TYPE = {
    "statute": "statute",
    "case": "case",
    "court": "court",
    "judge": "judge",
    "plaintiff": "plaintiff",
    "defendant": "defendant",
    "attorney": "attorney",
    "contract": "contract",
    "regulation": "regulation",
    "precedent": "precedent",
    "doctrine": "doctrine",
    "evidence": "evidence",
}

# Map frontmatter authority_level string -> AuthorityLevel enum.
_AUTHORITY_MAP = {
    "constitution": AuthorityLevel.CONSTITUTION,
    "statute": AuthorityLevel.STATUTE,
    "regulation": AuthorityLevel.REGULATION,
    "local_regulation": AuthorityLevel.LOCAL_REGULATION,
    "judicial_interpretation": AuthorityLevel.JUDICIAL_INTERPRETATION,
    "guiding_case": AuthorityLevel.GUIDING_CASE,
    "precedent": AuthorityLevel.PRECEDENT,
    "legal_scholarship": AuthorityLevel.LEGAL_SCHOLARSHIP,
    "local_guidance": AuthorityLevel.LOCAL_GUIDANCE,
}


def _node_type_for_page(fm: Dict, subdir: str) -> str:
    """Map a page's frontmatter + subdir to a graph node_type."""
    page_type = str(fm.get("type", "")).strip().lower()
    if page_type in _PAGE_TYPE_TO_NODE_TYPE:
        return _PAGE_TYPE_TO_NODE_TYPE[page_type]
    if page_type in LEGAL_ENTITY_TYPES:
        return page_type
    if subdir == "concepts":
        return "concept"
    if subdir == "summaries":
        return "document"
    return page_type or "other"


def _authority_for_page(fm: Dict) -> Optional[AuthorityLevel]:
    raw = str(fm.get("authority_level", "")).strip().lower()
    return _AUTHORITY_MAP.get(raw)


def _label_for(stem: str, fm: Dict) -> str:
    """A human label: prefer frontmatter description/title, fall back to stem."""
    desc = fm.get("description") or fm.get("title")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()
    return stem


def _parse_page(path: Path) -> Tuple[Dict, str]:
    """Return (frontmatter_dict, body) for a wiki page."""
    text = path.read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    if parts is None:
        return {}, text
    return parse_frontmatter(parts[0]), parts[1]


def _resolve_link_target(target: str, known_stems: Dict[str, str]) -> Optional[str]:
    """Resolve a wikilink target to a known page stem. Returns the stem or None."""
    target = target.strip()
    if not target:
        return None
    # [[subdir/name]] or [[name]]
    leaf = target.rsplit("/", 1)[-1]
    if leaf in known_stems:
        return leaf
    if target in known_stems:
        return target
    return None


def extract_graph_from_wiki(kb_dir: Path, *, clear: bool = False) -> LegalKnowledgeGraph:
    """Scan wiki pages and populate the legal knowledge graph.

    Each entity/concept/summary page -> a graph node; each ``[[wikilink]]``
    between two known pages -> a ``CITES`` edge (heuristic confidence 0.6).
    Idempotent. Pass ``clear=True`` to wipe the graph first.
    """
    graph = LegalKnowledgeGraph(kb_dir)
    if clear:
        graph._nodes.clear()
        graph._edges.clear()
        graph._outgoing_edges.clear()
        graph._incoming_edges.clear()
        graph._node_alias_index.clear()
        graph._type_index.clear()

    wiki = kb_dir / "wiki"
    page_dirs = ("entities", "concepts", "summaries")

    # Pass 1: register all pages as nodes.
    stem_to_node: Dict[str, object] = {}
    stem_to_page: Dict[str, str] = {}  # stem -> "subdir/stem"
    for subdir in page_dirs:
        d = wiki / subdir
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            stem = md.stem
            fm, _body = _parse_page(md)
            node_type = _node_type_for_page(fm, subdir)
            authority = _authority_for_page(fm)
            node = graph.find_or_create_node(
                label=_label_for(stem, fm),
                node_type=node_type,
                description=fm.get("description")
                if isinstance(fm.get("description"), str)
                else None,
                source_page=f"{subdir}/{stem}",
                authority_level=authority,
            )
            stem_to_node[stem] = node
            stem_to_page[stem] = f"{subdir}/{stem}"

    # Pass 2: wikilinks -> edges.
    for subdir in page_dirs:
        d = wiki / subdir
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            src_stem = md.stem
            src_node = stem_to_node.get(src_stem)
            if src_node is None:
                continue
            _fm, body = _parse_page(md)
            for m in _WIKILINK_RE.finditer(body):
                target_stem = _resolve_link_target(m.group(1), stem_to_page)
                if target_stem is None:
                    continue
                tgt_node = stem_to_node.get(target_stem)
                if tgt_node is None or tgt_node.node_id == src_node.node_id:
                    continue
                relation = _heuristic_relation(src_node.node_type, tgt_node.node_type)
                graph.add_edge(
                    source_id=src_node.node_id,
                    target_id=tgt_node.node_id,
                    relation_type=relation,
                    confidence=0.6,
                    source_page=f"{subdir}/{src_stem}",
                )
    return graph


def _heuristic_relation(src_type: str, tgt_type: str) -> RelationType:
    """Best-guess relation type from node types (LLM-typed extraction is Phase 3)."""
    if src_type == "case" and tgt_type in ("statute", "regulation"):
        return RelationType.CITES
    if src_type == "statute" and tgt_type == "statute":
        return RelationType.REVISES
    if src_type in ("precedent", "case") and tgt_type in ("precedent", "case"):
        return RelationType.SIMILAR_CASE
    if src_type == "concept" and tgt_type in ("statute", "case", "regulation"):
        return RelationType.APPLIES
    return RelationType.CITES  # generic "references"


def extract_doc_graph(kb_dir: Path, doc_name: str) -> LegalKnowledgeGraph:
    """Re-extract the graph after a doc compile.

    Phase 2 does a full idempotent re-scan (correctness over incrementality -
    the wiki is small per add). A future incremental extractor would track
    which pages belong to ``doc_name`` and upsert only those.
    """
    return extract_graph_from_wiki(kb_dir)


def graph_stats(kb_dir: Path) -> Dict:
    """Convenience: current graph stats."""
    return LegalKnowledgeGraph(kb_dir).stats()


# ----------------------------------------------------------------------------
# LLM-typed relation extraction (upgrade over the heuristic extractor)
# ----------------------------------------------------------------------------
_RELATION_VALUES = {rt.value for rt in RelationType}

_EXTRACT_PROMPT = """You are extracting a legal knowledge graph from a wiki page.
Given the page text, extract the entities and the TYPED relations between them.

Use ONLY these relation types: {relations}

Respond as JSON: {{"entities": [{{"label": "...", "type": "statute|case|court|..."}}],
"relations": [{{"source": "label", "relation": "cites", "target": "label", "confidence": 0.9}}]}}

Rules:
- source/target labels MUST match an entity label in "entities".
- relation MUST be one of the listed types.
- Omit relations you cannot type confidently.
- confidence is 0.0-1.0.

Page text:
"""


def _default_llm_extract(page_text: str, model: str) -> Dict[str, Any]:
    """Default LLM extraction via litellm. Returns parsed {entities, relations}."""
    import json
    import re

    import litellm

    prompt = _EXTRACT_PROMPT.format(relations=", ".join(sorted(_RELATION_VALUES)))
    prompt += page_text[:4000]
    resp = litellm.completion(
        model=model, messages=[{"role": "user", "content": prompt}], temperature=0.0,
    )
    text = resp["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"entities": [], "relations": []}
    try:
        return json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return {"entities": [], "relations": []}


def extract_graph_with_llm(
    kb_dir: Path,
    *,
    model: str = "gpt-4o-mini",
    llm_call=None,
    clear: bool = False,
) -> LegalKnowledgeGraph:
    """LLM-driven typed-relation extraction (spec section 3.7).

    For each knowledge page, an LLM extracts entities + typed relations
    (REVISES / APPLIES / CONTRADICTS / ...) from the page text - the typed
    graph the heuristic :func:`extract_graph_from_wiki` only approximates.
    Falls back to the heuristic extractor for pages where the LLM returns
    nothing. ``llm_call(page_text, model) -> {entities, relations}`` is
    injectable for tests.
    """
    graph = LegalKnowledgeGraph(kb_dir)
    if clear:
        for store in (graph._nodes, graph._edges, graph._outgoing_edges,
                      graph._incoming_edges, graph._node_alias_index, graph._type_index):
            store.clear()
    extract_fn = llm_call or _default_llm_extract
    wiki = kb_dir / "wiki"

    # Seed nodes from pages first (so relation targets resolve).
    extract_graph_from_wiki(kb_dir)

    for subdir in ("entities", "concepts", "summaries"):
        d = wiki / subdir
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            fm, body = _parse_page(md)
            try:
                result = extract_fn(body, model)
            except Exception:  # noqa: BLE001 - best-effort, fall back to heuristic edges
                continue
            label_to_node = {}
            for ent in result.get("entities", []) or []:
                label = str(ent.get("label", "")).strip()
                ntype = str(ent.get("type", "")).strip().lower() or "other"
                if not label:
                    continue
                node = graph.find_or_create_node(
                    label=label, node_type=ntype,
                    source_page=f"{subdir}/{md.stem}",
                )
                label_to_node[label] = node
            for rel in result.get("relations", []) or []:
                src = label_to_node.get(str(rel.get("source", "")).strip())
                tgt = label_to_node.get(str(rel.get("target", "")).strip())
                rtype = str(rel.get("relation", "")).strip().lower()
                if not (src and tgt and rtype in _RELATION_VALUES) or src.node_id == tgt.node_id:
                    continue
                try:
                    conf = float(rel.get("confidence", 0.8))
                except (TypeError, ValueError):
                    conf = 0.8
                rt = RelationType(rtype)
                graph.add_edge(src.node_id, tgt.node_id, rt, confidence=conf,
                               source_page=f"{subdir}/{md.stem}")
    return graph


__all__ = [
    "extract_graph_from_wiki", "extract_doc_graph", "graph_stats",
    "extract_graph_with_llm",
]
