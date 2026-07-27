"""Query Router - hybrid retrieval with RRF fusion (spec section 3.9).

A single ``index.md`` retrieval fails past ~100-200 pages; the Query Router
runs multi-recall and fuses:

- **BM25** (over DocIR node text, via :mod:`openkb.agent.docir_tools`) - the
  precise-term leg (statute numbers / case numbers must hit exactly).
- **Graph traversal** (via :mod:`openkb.agent.legal_tools` / the legal graph) -
  the structural-impact leg (what cites/applies this statute) that text cannot
  capture. Empty when the graph is unpopulated.
- **RRF** (Reciprocal Rank Fusion) - combines the rankings; per-retriever
  weights follow the routed intent (precise-term -> BM25-heavy, impact ->
  graph-heavy).

The vector leg (semantic embedding similarity) is optional: it activates only
when an embedding model is configured and reachable via litellm; otherwise the
router degrades gracefully to BM25 + graph. This keeps the router
dependency-free in the common case (spec: text-first).

Intent routing is rule-based (statute/case number patterns, impact keywords);
a learned router is out of scope for Phase 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openkb.agent.docir_tools import _BM25Index, _load_all_docir, _tokenize
from openkb.legal.graph import LegalKnowledgeGraph
from openkb.legal.schema import RelationType

# RRF constant (standard). rank positions are 1-based.
_RRF_K = 60


def _resolve_embedding_model(kb_root: Path) -> Optional[str]:
    """Read the embedding model from KB config, if any."""
    try:
        from openkb.config import resolve_effective_config

        config = resolve_effective_config(kb_root)[0]
        return config.get("embedding_model") or None
    except Exception:  # noqa: BLE001 - embedding leg is optional
        return None


def _cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity (0 if either is zero or lengths differ)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _default_embed(text: str, model: str) -> List[float]:
    """Default embedding via litellm."""
    import litellm

    resp = litellm.embedding(model=model, input=text)
    return list(resp["data"][0]["embedding"])


class QueryIntent(Enum):
    """Routed intent - determines retriever weights in RRF fusion."""

    PRECISE_TERM = "precise_term"  # 条文号/案号/专名 -> BM25 dominant
    IMPACT = "impact"  # "影响哪些案件" -> graph dominant
    SEMANTIC = "semantic"  # open legal question -> balanced
    VISUAL = "visual"  # involves figures/signatures -> BM25 + visual gate


# Per-intent retriever weights (BM25, GRAPH, VECTOR). The vector leg is
# optional - it degrades to no contribution when embeddings are unavailable.
_INTENT_WEIGHTS: Dict[QueryIntent, Tuple[float, float, float]] = {
    QueryIntent.PRECISE_TERM: (1.0, 0.2, 0.4),
    QueryIntent.IMPACT: (0.3, 1.0, 0.2),
    QueryIntent.SEMANTIC: (1.0, 0.6, 1.0),
    QueryIntent.VISUAL: (1.0, 0.4, 0.5),
}

# Patterns
_STATUTE_PATTERN = re.compile(r"第\s*\d+\s*条|民法典|刑法|合同法|司法解释|条例|规定")
_CASE_PATTERN = re.compile(r"\d{4}\s*[民刑行]\s*[初终再]\s*\d+号|（\d{4}）.*号|案号")
_IMPACT_PATTERN = re.compile(r"影响|哪些案件|影响面|受影响|波及|关联案件")
_VISUAL_PATTERN = re.compile(r"图|图表|签名|签章|印章|照片|扫描件|手写|流水图|现场图")


def route_intent(query: str) -> QueryIntent:
    """Rule-based intent routing."""
    if _IMPACT_PATTERN.search(query):
        return QueryIntent.IMPACT
    if _VISUAL_PATTERN.search(query) and not _STATUTE_PATTERN.search(query):
        return QueryIntent.VISUAL
    if _STATUTE_PATTERN.search(query) or _CASE_PATTERN.search(query):
        return QueryIntent.PRECISE_TERM
    return QueryIntent.SEMANTIC


@dataclass
class RankedNode:
    """A fused retrieval result."""

    node_id: str
    doc_name: str
    title: str
    score: float
    sources: List[str] = field(default_factory=list)  # which retrievers contributed


class QueryRouter:
    """Hybrid BM25 + graph + vector retrieval with RRF fusion."""

    def __init__(
        self,
        kb_root: Path | str,
        *,
        embedding_model: Optional[str] = None,
        embed_call: Optional[Any] = None,
    ) -> None:
        self.kb_root = Path(kb_root).resolve()
        self.wiki_root = self.kb_root / "wiki"
        self.embedding_model = embedding_model or _resolve_embedding_model(self.kb_root)
        # embed_call(text, model) -> List[float]; injectable for tests.
        self._embed_call = embed_call
        self._embed_cache: Dict[str, List[float]] = {}

    # -- retrievers --------------------------------------------------------

    def _bm25_ranking(self, query: str, limit: int = 30) -> List[Tuple[str, str, str, int]]:
        """BM25 over DocIR nodes. Returns [(doc_name, node_id, title, rank)]."""
        docs = _load_all_docir(self.wiki_root)
        if not docs:
            return []
        index = _BM25Index(docs)
        results = index.search(query, limit=limit)
        return [(doc, nid, title, rank) for rank, (doc, nid, title, _s) in enumerate(results, 1)]

    def _graph_ranking(self, query: str, limit: int = 30) -> List[Tuple[str, str, str, int]]:
        """Graph traversal from entities mentioned in the query."""
        graph = LegalKnowledgeGraph(self.kb_root)
        if not graph._nodes:
            return []
        # Find entities whose label appears in the query.
        q_lower = query.lower()
        seeds = [n for n in graph._nodes.values() if n.label and n.label.lower() in q_lower]
        if not seeds:
            # Fuzzy: tokenize query, match tokens in labels.
            q_tokens = set(_tokenize(query))
            seeds = [
                n for n in graph._nodes.values() if n.label and q_tokens & set(_tokenize(n.label))
            ]
        if not seeds:
            return []
        # Traverse both directions from each seed; rank reachable nodes by
        # cumulative weight. Forward (outgoing) covers "this case cites what";
        # reverse (incoming, via find_affecting_nodes) covers "what cites this"
        # - the impact question that pure forward traversal misses.
        scores: Dict[str, float] = {}
        titles: Dict[str, str] = {}
        doc_names: Dict[str, str] = {}
        affect_relations = [
            RelationType.CITES,
            RelationType.APPLIES,
            RelationType.INTERPRETS,
            RelationType.FOLLOWS,
            RelationType.SIMILAR_CASE,
            RelationType.REVISES,
        ]
        for seed in seeds:
            # Forward (outgoing).
            traversal = graph.traverse(
                seed.node_id,
                relation_types=affect_relations,
                max_depth=2,
                min_confidence=0.4,
            )
            for r in traversal:
                if r.depth == 0:
                    continue
                key = r.node.source_page or r.node.node_id
                scores[key] = scores.get(key, 0.0) + r.total_weight
                titles[key] = r.node.label
                doc_names[key] = r.node.source_doc or r.node.node_type
            # Reverse (incoming - who references this seed).
            for r in graph.find_affecting_nodes(seed.node_id):
                key = r.node.source_page or r.node.node_id
                scores[key] = scores.get(key, 0.0) + r.total_weight * 0.5
                titles.setdefault(key, r.node.label)
                doc_names.setdefault(key, r.node.source_doc or r.node.node_type)
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [
            (doc_names.get(nid, ""), nid, titles.get(nid, ""), rank)
            for rank, (nid, _s) in enumerate(ranked, 1)
        ]

    # -- fusion ------------------------------------------------------------

    def _vector_ranking(self, query: str, limit: int = 30) -> List[Tuple[str, str, str, int]]:
        """Semantic vector leg - embedding cosine similarity (optional).

        Activates only when an embedding model is reachable (via litellm or the
        injected ``embed_call``). Embeddings are cached on disk by content hash
        so repeat queries don't re-embed the corpus. Degrades to [] (no
        contribution) on any failure - the router stays BM25+graph.
        """
        if not self.embedding_model and not self._embed_call:
            return []
        try:
            q_vec = self._embed(query)
        except Exception:  # noqa: BLE001 - degrade gracefully
            return []
        if not q_vec:
            return []
        docs = _load_all_docir(self.wiki_root)
        scored: List[Tuple[str, str, str, float]] = []
        for doc in docs:
            for nid, node in doc.node_table().items():
                if node.is_visual() or node.kind == "document":
                    continue
                text = f"{node.title or ''} {node.text or ''}".strip()
                if not text:
                    continue
                try:
                    n_vec = self._embed(text)
                except Exception:  # noqa: BLE001
                    continue
                sim = _cosine(q_vec, n_vec)
                if sim > 0:
                    scored.append((doc.doc_name, nid, node.title or "", sim))
        scored.sort(key=lambda x: x[3], reverse=True)
        return [
            (doc, nid, title, rank)
            for rank, (doc, nid, title, _s) in enumerate(scored[:limit], 1)
        ]

    def _embed(self, text: str) -> List[float]:
        """Embed text, cached on disk by sha256(text)+model."""
        import hashlib
        import json

        key = hashlib.sha256(f"{self.embedding_model}:{text}".encode("utf-8")).hexdigest()
        if key in self._embed_cache:
            return self._embed_cache[key]
        cache_dir = self.kb_root / ".openkb" / "embeddings"
        cache_file = cache_dir / f"{key}.json"
        if cache_file.exists():
            vec = json.loads(cache_file.read_text(encoding="utf-8"))
            self._embed_cache[key] = vec
            return vec
        if self._embed_call is not None:
            vec = self._embed_call(text, self.embedding_model or "default")
        else:
            vec = _default_embed(text, self.embedding_model)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(vec), encoding="utf-8")
        self._embed_cache[key] = vec
        return vec

    def search(
        self, query: str, *, intent: Optional[QueryIntent] = None, limit: int = 10
    ) -> List[RankedNode]:
        """Run multi-recall (BM25 + graph + vector) + RRF fusion."""
        intent = intent or route_intent(query)
        w_bm25, w_graph, w_vector = _INTENT_WEIGHTS[intent]

        bm25 = self._bm25_ranking(query) if w_bm25 > 0 else []
        graph = self._graph_ranking(query) if w_graph > 0 else []
        vector = self._vector_ranking(query) if w_vector > 0 else []

        # RRF fusion with per-retriever weights.
        fused: Dict[str, float] = {}
        meta: Dict[str, Tuple[str, str, List[str]]] = {}
        for ranking, weight, tag in (
            (bm25, w_bm25, "bm25"), (graph, w_graph, "graph"), (vector, w_vector, "vector"),
        ):
            for doc, nid, title, rank in ranking:
                score = weight * (1.0 / (_RRF_K + rank))
                fused[nid] = fused.get(nid, 0.0) + score
                prev = meta.get(nid, (doc, title, []))
                meta[nid] = (prev[0] or doc, prev[1] or title, prev[2] + [tag])

        ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [
            RankedNode(
                node_id=nid,
                doc_name=meta[nid][0],
                title=meta[nid][1],
                score=round(s, 5),
                sources=meta[nid][2],
            )
            for nid, s in ranked
        ]

    def search_formatted(self, query: str, *, limit: int = 10) -> str:
        """Render search results as a readable string for the agent."""
        intent = route_intent(query)
        results = self.search(query, intent=intent, limit=limit)
        if not results:
            return f"No results for: {query} (intent={intent.value})"
        lines = [f"[intent={intent.value}] {len(results)} result(s) for: {query}"]
        for i, r in enumerate(results, 1):
            sources = "+".join(r.sources)
            lines.append(f"{i}. [{r.doc_name}] {r.node_id} ({r.score:.5f}, {sources}) {r.title}")
        return "\n".join(lines) + "\n"


def route_query(query: str, kb_root: Path | str, *, limit: int = 10) -> List[RankedNode]:
    """Convenience: route + search in one call."""
    return QueryRouter(kb_root).search(query, limit=limit)


__all__ = ["QueryIntent", "RankedNode", "QueryRouter", "route_intent", "route_query"]
