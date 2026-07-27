"""DocIR read tools for the OpenKB agent.

Domain-agnostic retrieval over the canonical DocIR (``wiki/sources/*.docir.json``).
These complement :mod:`openkb.agent.tools` (which reads the legacy ``.md`` /
``.json`` source artifacts) with node-level access:

- :func:`read_node` - fetch a node by id / typed URI (``docir://`` / ``law://``
  / ``case://``). The existence-gate basis for citation verification.
- :func:`search_docir` - a dependency-free BM25 ranker over all DocIR node text.
  Returns candidate node ids (the Query Router's BM25 leg).
- :func:`render_page` - the Vision Tool gate. Returns the visual nodes on a page
  + a high-DPI rendered image (text-first: if a page has no visual content, the
  agent is told text retrieval suffices and no image is rendered).

Read paths are domain-agnostic: ``read_node`` / ``search_docir`` contain no
legal logic. Legal routing (statute numbers -> BM25, impact -> graph traversal)
is layered above by the Query Router, not here.
"""

from __future__ import annotations

import base64
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openkb.docir import DocIRDocument

# ----------------------------------------------------------------------------
# DocIR loading (with a per-process mtime cache so repeated reads are cheap)
# ----------------------------------------------------------------------------
_DOC_CACHE: Dict[str, Tuple[float, DocIRDocument]] = {}


def _load_all_docir(wiki_root: Path) -> List[DocIRDocument]:
    """Load every ``.docir.json`` under ``wiki/sources/`` (mtime-cached)."""
    sources_dir = wiki_root / "sources"
    if not sources_dir.is_dir():
        return []
    docs: List[DocIRDocument] = []
    for path in sorted(sources_dir.glob("*.docir.json")):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        cached = _DOC_CACHE.get(str(path))
        if cached and cached[0] == mtime:
            docs.append(cached[1])
            continue
        try:
            doc = DocIRDocument.load(path)
        except (OSError, ValueError, KeyError):
            continue
        _DOC_CACHE[str(path)] = (mtime, doc)
        docs.append(doc)
    return docs


def _load_docir(doc_name: str, wiki_root: Path) -> Optional[DocIRDocument]:
    """Load a single doc's DocIR by name (mtime-cached)."""
    path = wiki_root / "sources" / f"{doc_name}.docir.json"
    if not path.exists():
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cached = _DOC_CACHE.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        doc = DocIRDocument.load(path)
    except (OSError, ValueError, KeyError):
        return None
    _DOC_CACHE[str(path)] = (mtime, doc)
    return doc


# ----------------------------------------------------------------------------
# read_node - fetch a node by id / typed URI (existence gate basis)
# ----------------------------------------------------------------------------
def read_node(node_id: str, wiki_root: str) -> str:
    """Read a DocIR node by id or typed URI.

    Resolves ``docir://`` (against ``anchors.default``) and ``law://`` /
    ``case://`` (against ``anchors.legal``) across every ``.docir.json`` in
    ``wiki/sources/``. Returns the node's text + location + provenance +
    anchors, or ``"Node not found: {node_id}"``.

    Args:
        node_id: A ``docir://`` / ``law://`` / ``case://`` URI (or raw node id).
        wiki_root: Absolute path to the wiki root directory.

    Returns:
        Formatted node content, or a not-found message.
    """
    root = Path(wiki_root).resolve()
    docs = _load_all_docir(root)
    for doc in docs:
        node = doc.resolve_uri(node_id)
        if node is not None:
            return _format_node(node, doc)
    return f"Node not found: {node_id}"


def _format_node(node: Any, doc: DocIRDocument) -> str:
    """Render a DocIR node as a readable string for the agent."""
    parts: List[str] = []
    parts.append(f"[{doc.doc_name}] {node.kind}: {node.title or '(untitled)'}")
    parts.append(f"id: {node.id}")
    if node.anchors and node.anchors.legal:
        parts.append(f"legal_anchor: {node.anchors.legal}")
    if node.loc:
        loc_bits = []
        if node.loc.page is not None:
            loc_bits.append(f"page={node.loc.page}")
        if node.loc.char_start is not None:
            loc_bits.append(f"chars={node.loc.char_start}-{node.loc.char_end}")
        if loc_bits:
            parts.append(f"loc: {', '.join(loc_bits)}")
    parts.append(
        f"provenance: extractor={node.provenance.extractor} "
        f"confidence={node.provenance.confidence:.2f} verified={node.provenance.verified}"
    )
    if node.text:
        parts.append("")
        parts.append(node.text)
    if node.is_visual() and node.vision:
        v = node.vision
        parts.append(f"[visual: type={v.type} analyzed={v.analyzed} render_ref={v.render_ref}]")
        if v.text_anchor:
            parts.append(f"text_anchor: {v.text_anchor}")
    return "\n".join(parts) + "\n"


# ----------------------------------------------------------------------------
# search_docir - dependency-free BM25 over DocIR node text
# ----------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]")


def _tokenize(text: str) -> List[str]:
    """Tokenize for BM25: ASCII words + CJK per-character.

    Chinese has no word boundaries without a segmenter; per-character tokenization
    is a robust, dependency-free fallback that still lets substring queries
    (``577`` -> the char ``5``/``7``/``7`` or the run ``577``) match statute
    numbers. ASCII runs are kept whole so ``LPR`` / ``577`` match as units.
    """
    if not text:
        return []
    tokens: List[str] = []
    for match in _TOKEN_RE.finditer(text):
        tok = match.group(0)
        if tok[0].isascii():
            tokens.append(tok.lower())
        else:
            tokens.append(tok)
    return tokens


class _BM25Index:
    """A tiny in-memory BM25 index over a set of DocIR nodes."""

    def __init__(self, docs: List[DocIRDocument], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.entries: List[
            Tuple[str, str, str, List[str]]
        ] = []  # (doc_name, node_id, title, tokens)
        self.doc_freq: Dict[str, int] = {}
        self.avg_len: float = 0.0
        for doc in docs:
            for nid, node in doc.node_table().items():
                # Skip pure visual nodes (no text to match) and the document root.
                if node.is_visual() or node.kind == "document":
                    continue
                text = f"{node.title or ''} {node.text or ''}"
                toks = _tokenize(text)
                if not toks:
                    continue
                self.entries.append((doc.doc_name, nid, node.title or "", toks))
                for term in set(toks):
                    self.doc_freq[term] = self.doc_freq.get(term, 0) + 1
        total_len = sum(len(e[3]) for e in self.entries)
        self.avg_len = total_len / len(self.entries) if self.entries else 1.0

    def search(self, query: str, limit: int = 10) -> List[Tuple[str, str, str, float]]:
        q_terms = _tokenize(query)
        if not q_terms or not self.entries:
            return []
        n = len(self.entries)
        scored: List[Tuple[str, str, str, float]] = []
        for doc_name, nid, title, toks in self.entries:
            tf: Dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            score = 0.0
            dl = len(toks)
            for term in q_terms:
                if term not in tf:
                    continue
                df = self.doc_freq.get(term, 0)
                if df == 0:
                    continue
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                f = tf[term]
                score += (
                    idf
                    * (f * (self.k1 + 1))
                    / (f + self.k1 * (1 - self.b + self.b * dl / self.avg_len))
                )
            if score > 0:
                scored.append((doc_name, nid, title, score))
        scored.sort(key=lambda x: x[3], reverse=True)
        return scored[:limit]


def search_docir(query: str, wiki_root: str, mode: str = "bm25", limit: int = 10) -> str:
    """BM25 search over all DocIR node text. Returns ranked candidate node ids.

    Args:
        query: Search terms (Chinese chars tokenized per-character; ASCII words
            kept whole, so ``"民法典第577条"`` / ``"577"`` match statute nodes).
        wiki_root: Absolute path to the wiki root directory.
        mode: Retrieval mode (currently only ``"bm25"``; ``"vector"`` reserved
            for Phase 2).
        limit: Max results.

    Returns:
        Newline-separated ``rank. [doc] node_id (score) title`` lines, or
        ``"No results for: {query}"``.
    """
    root = Path(wiki_root).resolve()
    docs = _load_all_docir(root)
    if not docs:
        return f"No results for: {query}"
    index = _BM25Index(docs)
    results = index.search(query, limit=limit)
    if not results:
        return f"No results for: {query}"
    lines: List[str] = []
    for rank, (doc_name, nid, title, score) in enumerate(results, 1):
        lines.append(f"{rank}. [{doc_name}] {nid} ({score:.3f}) {title}")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------
# render_page - Vision Tool gate (text-first, vision-second)
# ----------------------------------------------------------------------------
_MIME_BY_SUFFIX = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def render_page(doc_name: str, page: str, kb_root: str) -> dict:
    """Render a document page for on-demand visual analysis (Vision Tool gate).

    Text-first: if the page has no registered visual nodes, returns a text
    message telling the agent text retrieval suffices (no image rendered, no
    vision cost). If visual nodes exist, renders the page from the raw PDF to a
    high-DPI PNG and returns it as a base64 data URL plus the visual-node
    metadata (type / text_anchor / render_ref) so the agent can reason about
    what's on the page.

    Args:
        doc_name: Document name (without extension).
        page: Page number (e.g. ``"47"``).
        kb_root: Absolute path to the KB root directory.

    Returns:
        A dict with ``type``/``text`` (no visual content / error) or
        ``type``/``image_url``/``visual_nodes`` (rendered page for analysis).
    """
    root = Path(kb_root).resolve()
    try:
        page_num = int(str(page).strip())
    except ValueError:
        return {"type": "text", "text": f"Invalid page number: {page}"}

    doc = _load_docir(doc_name, root / "wiki")
    if doc is None:
        return {"type": "text", "text": f"DocIR not found for document: {doc_name}"}

    visual_nodes = [n for n in doc.get_nodes_by_page(page_num) if n.is_visual()]
    if not visual_nodes:
        return {
            "type": "text",
            "text": (
                f"Page {page_num} of {doc_name} has no registered visual content; "
                "text retrieval is sufficient (text-first policy)."
            ),
        }

    # Render the page from the raw PDF to a high-DPI PNG.
    image_url = _render_pdf_page(root, doc_name, page_num)
    if image_url is None:
        # No raw PDF available - fall back to the registered render_ref pointer.
        render_refs = [
            n.vision.render_ref for n in visual_nodes if n.vision and n.vision.render_ref
        ]
        return {
            "type": "text",
            "text": (
                f"Page {page_num} of {doc_name} has {len(visual_nodes)} visual node(s) "
                f"but no raw PDF to render. render_refs: {render_refs}"
            ),
            "visual_nodes": _visual_node_briefs(visual_nodes),
        }

    return {
        "type": "image",
        "image_url": image_url,
        "visual_nodes": _visual_node_briefs(visual_nodes),
    }


def _visual_node_briefs(nodes: List[Any]) -> List[Dict[str, Any]]:
    """Compact metadata for each visual node on the page."""
    briefs: List[Dict[str, Any]] = []
    for n in nodes:
        v = n.vision
        briefs.append(
            {
                "id": n.id,
                "type": v.type if v else "unknown",
                "text_anchor": v.text_anchor if v else None,
                "render_ref": v.render_ref if v else None,
                "analyzed": v.analyzed if v else False,
            }
        )
    return briefs


def _render_pdf_page(kb_root: Path, doc_name: str, page_num: int) -> Optional[str]:
    """Render ``page_num`` of ``raw/<doc_name>.pdf`` to a base64 PNG data URL.

    High DPI (3x zoom) so chart labels and signatures stay legible. Returns
    None if pymupdf is unavailable or the raw PDF doesn't exist.
    """
    raw_pdf = kb_root / "raw" / f"{doc_name}.pdf"
    if not raw_pdf.exists():
        return None
    try:
        import pymupdf
    except ImportError:
        return None
    try:
        with pymupdf.open(str(raw_pdf)) as doc:
            if page_num < 1 or page_num > doc.page_count:
                return None
            page = doc[page_num - 1]
            # 3x zoom ~= 216 DPI (72 base * 3). Enough for small chart labels.
            matrix = pymupdf.Matrix(3.0, 3.0)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            png_bytes = pix.tobytes("png")
    except Exception:
        return None
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


__all__ = ["read_node", "search_docir", "render_page"]
