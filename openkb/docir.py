"""DocIR - Document Intermediate Representation (domain-agnostic base).

Implements the canonical format defined in ``spec/docir-format.md``: a recursive
node tree that unifies heterogeneous documents (txt/md/html/pdf/word/image)
into a computable, addressable, traceable representation.

This module is the **domain-agnostic core**. Legal and other verticals extend
it via ``extensions`` (e.g. ``extensions.legal``) and typed ``anchors`` (e.g.
``anchors.legal = law://...``) WITHOUT polluting the core. Strip the legal
overlay and this is still a complete generic document representation.

Key design points (per spec):
- A document = a node tree. Short docs are shallow (root -> paragraphs); long
  docs are deep (root -> parts/chapters/sections -> articles/paragraphs). Same
  shape, different depth.
- Each node carries identity (``id``), content (``title``/``text``/``children``),
  location (``loc``: page/char/bbox), and provenance (``extractor``/``confidence``/
  ``verified``). ``id`` is the query key for citation verification.
- Visual nodes (``kind == figure_anchor``) register a ``vision`` field with a
  render pointer + surrounding text anchor but NO pre-generated description -
  analysis is on-demand at query time (Harvey: ~90% of figures are never queried).
- ``page_map`` (page -> node ids) is derived from the tree for O(1) candidate-page
  lookup by the Vision Tool.
- Citation verification routes by URI scheme: ``docir://`` -> ``anchors.default``,
  ``law://``/``case://`` -> ``anchors.legal`` (injected by the legal overlay).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ----------------------------------------------------------------------------
# Schema version
# ----------------------------------------------------------------------------
DOCIR_VERSION = "1.0"

# ----------------------------------------------------------------------------
# Node kind constants (open set - core passes unknown kinds through as strings)
# ----------------------------------------------------------------------------
# Generic kinds (core). Legal overlay adds: article / judgment_main / evidence.
KIND_DOCUMENT = "document"
KIND_SECTION = "section"
KIND_PARAGRAPH = "paragraph"
KIND_LIST_ITEM = "list_item"
KIND_TABLE = "table"
KIND_FIGURE_ANCHOR = "figure_anchor"
KIND_PAGE_MARKER = "page_marker"

_GENERIC_KINDS = {
    KIND_DOCUMENT,
    KIND_SECTION,
    KIND_PARAGRAPH,
    KIND_LIST_ITEM,
    KIND_TABLE,
    KIND_FIGURE_ANCHOR,
    KIND_PAGE_MARKER,
}

# ----------------------------------------------------------------------------
# Extractor constants (provenance.extractor)
# ----------------------------------------------------------------------------
EXTRACTOR_PAGEINDEX_TREE = "pageindex-tree"
EXTRACTOR_PYMUPDF_TEXT = "pymupdf-text"
EXTRACTOR_MARKITDOWN = "markitdown"
EXTRACTOR_MD_PARSER = "md-parser"
EXTRACTOR_OCR = "ocr"
EXTRACTOR_PDF_FIGURE_DETECT = "pdf-figure-detect"
EXTRACTOR_LLM_SUMMARY = "llm-summary"

# Visual content types (vision.type)
VISION_CHART = "chart"
VISION_SIGNATURE = "signature"
VISION_PHOTO = "photo"
VISION_HANDWRITING = "handwriting"
VISION_TABLE_COMPLEX = "table-complex"

# URI schemes (anchors)
SCHEME_DOCIR = "docir"
SCHEME_LAW = "law"
SCHEME_CASE = "case"


# ----------------------------------------------------------------------------
# Location - physical root for traceability
# ----------------------------------------------------------------------------
@dataclass
class DocIRLoc:
    """Original location: page / char offset / bbox.

    Short docs may omit ``page`` (single-page or no page concept). ``bbox`` is
    ``[x0, y0, x1, y1]`` in PDF points (or pixel coords for images).
    """

    page: Optional[int] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    bbox: Optional[List[float]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        if self.page is not None:
            d["page"] = self.page
        if self.char_start is not None:
            d["char_start"] = self.char_start
        if self.char_end is not None:
            d["char_end"] = self.char_end
        if self.bbox is not None:
            d["bbox"] = self.bbox
        return d

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["DocIRLoc"]:
        if not data:
            return None
        return cls(
            page=data.get("page"),
            char_start=data.get("char_start"),
            char_end=data.get("char_end"),
            bbox=data.get("bbox"),
        )


# ----------------------------------------------------------------------------
# Provenance - how the node was extracted
# ----------------------------------------------------------------------------
@dataclass
class DocIRProvenance:
    """Extraction provenance.

    Deterministic extractors (pymupdf-text / markitdown) carry confidence 1.0;
    LLM extractors (pageindex-tree) carry the model's self-reported confidence.
    ``verified`` flips to True after the citation-verification pipeline passes.
    """

    extractor: str = EXTRACTOR_MD_PARSER
    confidence: float = 1.0
    verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "extractor": self.extractor,
            "confidence": self.confidence,
            "verified": self.verified,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DocIRProvenance":
        if not data:
            return cls()
        return cls(
            extractor=data.get("extractor", EXTRACTOR_MD_PARSER),
            confidence=float(data.get("confidence", 1.0)),
            verified=bool(data.get("verified", False)),
        )


# ----------------------------------------------------------------------------
# Anchors - typed URIs (scheme-routed citation verification)
# ----------------------------------------------------------------------------
@dataclass
class DocIRAnchors:
    """Typed URI anchors.

    Core only populates ``default`` (a ``docir://`` URI). Verticals inject
    domain URIs (legal: ``law://`` / ``case://``). Verification routes by scheme.
    """

    default: str
    legal: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"default": self.default}
        if self.legal is not None:
            d["legal"] = self.legal
        return d

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["DocIRAnchors"]:
        if not data:
            return None
        return cls(default=data.get("default", ""), legal=data.get("legal"))

    def by_scheme(self, scheme: str) -> Optional[str]:
        """Return the anchor URI for ``scheme`` (``docir``/``law``/``case``)."""
        if scheme == SCHEME_DOCIR:
            return self.default
        if scheme == SCHEME_LAW or scheme == SCHEME_CASE:
            return self.legal
        return None


# ----------------------------------------------------------------------------
# Vision - on-demand visual analysis metadata (figure_anchor nodes only)
# ----------------------------------------------------------------------------
@dataclass
class DocIRVision:
    """Visual content registration - NO pre-analysis.

    Per Harvey: a context-free image description is expensive and lossy, and
    ~90% of figures are never queried. So we register only the type, a
    surrounding text anchor (the key to text-first retrieval), and a render
    pointer. Analysis happens on-demand and its result is cached in
    ``last_analysis`` so the next similar query reuses it.
    """

    type: str = VISION_PHOTO
    text_anchor: Optional[str] = None
    render_ref: Optional[str] = None
    analyzed: bool = False
    last_analysis: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "type": self.type,
            "analyzed": self.analyzed,
        }
        if self.text_anchor is not None:
            d["text_anchor"] = self.text_anchor
        if self.render_ref is not None:
            d["render_ref"] = self.render_ref
        if self.last_analysis is not None:
            d["last_analysis"] = self.last_analysis
        return d

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["DocIRVision"]:
        if not data:
            return None
        return cls(
            type=data.get("type", VISION_PHOTO),
            text_anchor=data.get("text_anchor"),
            render_ref=data.get("render_ref"),
            analyzed=bool(data.get("analyzed", False)),
            last_analysis=data.get("last_analysis"),
        )


# ----------------------------------------------------------------------------
# Node - the recursive tree unit
# ----------------------------------------------------------------------------
@dataclass
class DocIRNode:
    """A DocIR node - the recursive unit of the document tree.

    Carries identity (``id``), content (``title``/``text``/``children``),
    location (``loc``), provenance, typed anchors, and (for figure_anchor
    nodes) vision metadata. ``children`` is an embedded recursive list - the
    tree is the canonical shape; short docs are shallow, long docs are deep.
    """

    id: str
    kind: str = KIND_PARAGRAPH
    title: Optional[str] = None
    text: str = ""
    children: List["DocIRNode"] = field(default_factory=list)
    loc: Optional[DocIRLoc] = None
    provenance: DocIRProvenance = field(default_factory=DocIRProvenance)
    anchors: Optional[DocIRAnchors] = None
    vision: Optional[DocIRVision] = None

    def is_visual(self) -> bool:
        """True for figure_anchor nodes (carry vision metadata)."""
        return self.kind == KIND_FIGURE_ANCHOR

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
        }
        if self.title is not None:
            d["title"] = self.title
        d["text"] = self.text
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        else:
            d["children"] = []
        if self.loc is not None:
            d["loc"] = self.loc.to_dict()
        d["provenance"] = self.provenance.to_dict()
        if self.anchors is not None:
            d["anchors"] = self.anchors.to_dict()
        if self.vision is not None:
            d["vision"] = self.vision.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DocIRNode":
        children = [cls.from_dict(c) for c in data.get("children", []) or []]
        return cls(
            id=data["id"],
            kind=data.get("kind", KIND_PARAGRAPH),
            title=data.get("title"),
            text=data.get("text", ""),
            children=children,
            loc=DocIRLoc.from_dict(data.get("loc")),
            provenance=DocIRProvenance.from_dict(data.get("provenance")),
            anchors=DocIRAnchors.from_dict(data.get("anchors")),
            vision=DocIRVision.from_dict(data.get("vision")),
        )


# ----------------------------------------------------------------------------
# Source / Format - document-level metadata
# ----------------------------------------------------------------------------
@dataclass
class DocIRSource:
    """Origin of the document."""

    origin_type: str = "file"  # file | url | connector | sync
    origin_uri: str = ""
    content_hash: Optional[str] = None
    ingested_at: Optional[str] = None  # ISO-8601
    sync_source: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "origin_type": self.origin_type,
            "origin_uri": self.origin_uri,
        }
        if self.content_hash is not None:
            d["content_hash"] = self.content_hash
        if self.ingested_at is not None:
            d["ingested_at"] = self.ingested_at
        d["sync_source"] = self.sync_source
        return d

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DocIRSource":
        data = data or {}
        return cls(
            origin_type=data.get("origin_type", "file"),
            origin_uri=data.get("origin_uri", ""),
            content_hash=data.get("content_hash"),
            ingested_at=data.get("ingested_at"),
            sync_source=data.get("sync_source"),
        )


@dataclass
class DocIRFormat:
    """Input format + converter that produced this DocIR."""

    input_type: str = "txt"  # pdf | txt | md | html | docx | pptx | jpeg | png | ...
    input_meta: Dict[str, Any] = field(default_factory=dict)
    converter: str = EXTRACTOR_MARKITDOWN
    converter_version: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_type": self.input_type,
            "input_meta": self.input_meta,
            "converter": self.converter,
            "converter_version": self.converter_version,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DocIRFormat":
        data = data or {}
        return cls(
            input_type=data.get("input_type", "txt"),
            input_meta=data.get("input_meta", {}),
            converter=data.get("converter", EXTRACTOR_MARKITDOWN),
            converter_version=data.get("converter_version", ""),
        )


# ----------------------------------------------------------------------------
# Document - the full DocIR
# ----------------------------------------------------------------------------
@dataclass
class DocIRDocument:
    """A complete DocIR document = a node tree + derived indices.

    ``page_map`` (page -> {node_ids, render_ref}) is derived from the tree for
    O(1) candidate-page lookup by the Vision Tool. ``vision_nodes`` is the list
    of figure_anchor node ids. ``extensions`` holds domain overlays (e.g.
    ``{legal: {effective_status: current}}``); the core never reads it.
    """

    doc_id: str
    doc_name: str
    title: Optional[str] = None
    source: DocIRSource = field(default_factory=DocIRSource)
    format: DocIRFormat = field(default_factory=DocIRFormat)
    root: Optional[DocIRNode] = None
    page_map: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    vision_nodes: List[str] = field(default_factory=list)
    extensions: Dict[str, Any] = field(default_factory=dict)
    docir_version: str = DOCIR_VERSION

    # -- node access --------------------------------------------------------

    def node_table(self) -> Dict[str, DocIRNode]:
        """Flatten the tree into an ``{id: node}`` map (existence-gate basis)."""
        table: Dict[str, DocIRNode] = {}
        if self.root is None:
            return table
        stack = [self.root]
        while stack:
            node = stack.pop()
            table[node.id] = node
            stack.extend(node.children)
        return table

    def get_node(self, node_id: str) -> Optional[DocIRNode]:
        """Fetch a node by id (linear over the tree; cache node_table for bulk)."""
        return self.node_table().get(node_id)

    def get_nodes_by_page(self, page: int) -> List[DocIRNode]:
        """All nodes on ``page`` - O(1) via ``page_map``."""
        table = self.node_table()
        entry = self.page_map.get(str(page))
        if not entry:
            return []
        return [table[nid] for nid in entry.get("node_ids", []) if nid in table]

    def get_visual_nodes(self) -> List[DocIRNode]:
        """All figure_anchor nodes (candidates for on-demand vision analysis)."""
        table = self.node_table()
        return [table[nid] for nid in self.vision_nodes if nid in table]

    def resolve_uri(self, uri: str) -> Optional[DocIRNode]:
        """Resolve a typed URI to a node - the existence gate's lookup.

        Routes by scheme: ``docir://`` matches ``anchors.default`` (or ``id``);
        ``law://``/``case://`` match ``anchors.legal``.
        """
        scheme = _uri_scheme(uri)
        if scheme is None:
            return None
        for nid, node in self.node_table().items():
            if scheme == SCHEME_DOCIR:
                if nid == uri or (node.anchors and node.anchors.default == uri):
                    return node
            else:
                if node.anchors and node.anchors.legal == uri:
                    return node
        return None

    def has_visual_content(self) -> bool:
        """Whether the document contains any registered visual nodes."""
        return len(self.vision_nodes) > 0

    def attach_visual_node(
        self,
        *,
        page: Optional[int] = None,
        visual_type: str = VISION_PHOTO,
        text_anchor: Optional[str] = None,
        render_ref: Optional[str] = None,
        title: Optional[str] = None,
        bbox: Optional[List[float]] = None,
        parent_id: Optional[str] = None,
        extractor: str = EXTRACTOR_PDF_FIGURE_DETECT,
        confidence: float = 1.0,
    ) -> str:
        """Attach a figure_anchor node to the tree (post-build).

        Used by converters/indexers that build the text tree first, then
        register extracted figures. The node is attached under ``parent_id``
        (default: the root) and indices are rebuilt. Returns the new node id.
        """
        nid = make_node_id(
            self.doc_name,
            "visual",
            f"p{page if page is not None else 'x'}-{len(self.vision_nodes) + 1}",
        )
        node = DocIRNode(
            id=nid,
            kind=KIND_FIGURE_ANCHOR,
            title=title,
            text="",
            loc=DocIRLoc(page=page, bbox=bbox),
            provenance=DocIRProvenance(extractor=extractor, confidence=confidence),
            anchors=DocIRAnchors(default=nid),
            vision=DocIRVision(type=visual_type, text_anchor=text_anchor, render_ref=render_ref),
        )
        parent = self.get_node(parent_id) if parent_id else self.root
        if parent is None:
            self.root = node
        else:
            parent.children.append(node)
        self.rebuild_indices()
        return nid

    # -- index rebuild ------------------------------------------------------

    def rebuild_indices(self) -> "DocIRDocument":
        """Recompute ``page_map`` and ``vision_nodes`` from the tree.

        Call after mutating the tree directly. The builder calls this on
        ``build()`` so constructed documents are always consistent.
        """
        self.page_map = build_page_map(self.root) if self.root else {}
        self.vision_nodes = collect_vision_node_ids(self.root) if self.root else []
        return self

    # -- extension access ---------------------------------------------------

    def get_extension(self, name: str) -> Dict[str, Any]:
        """Read a domain overlay (e.g. ``get_extension('legal')``). Empty if unset."""
        ext = self.extensions.get(name)
        return ext if isinstance(ext, dict) else {}

    def set_extension(self, name: str, data: Dict[str, Any]) -> None:
        """Write a domain overlay (core never reads this)."""
        self.extensions[name] = data

    # -- serialization ------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "docir_version": self.docir_version,
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "title": self.title,
            "source": self.source.to_dict(),
            "format": self.format.to_dict(),
            "root": self.root.to_dict() if self.root else None,
            "page_map": self.page_map,
            "vision_nodes": self.vision_nodes,
            "extensions": self.extensions,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DocIRDocument":
        root_data = data.get("root")
        return cls(
            docir_version=data.get("docir_version", DOCIR_VERSION),
            doc_id=data["doc_id"],
            doc_name=data.get("doc_name", data["doc_id"]),
            title=data.get("title"),
            source=DocIRSource.from_dict(data.get("source")),
            format=DocIRFormat.from_dict(data.get("format")),
            root=DocIRNode.from_dict(root_data) if root_data else None,
            page_map=data.get("page_map", {}),
            vision_nodes=data.get("vision_nodes", []),
            extensions=data.get("extensions", {}),
        )

    def save(self, file_path: Path) -> None:
        """Write the canonical single-JSON representation.

        Uses an atomic temp-file + rename when running inside the OpenKB
        pipeline (``openkb.locks`` available) so a crash mid-write can never
        leave a truncated ``.docir.json`` for a concurrent lint/recompile scan
        to read - satisfying the "wiki writes go through locks.py" invariant.
        Falls back to a plain write in pure-stdlib contexts (unit tests).
        """
        file_path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        try:
            from openkb.locks import atomic_write_text

            atomic_write_text(file_path, data)
        except ImportError:
            file_path.write_text(data, encoding="utf-8")

    @classmethod
    def load(cls, file_path: Path) -> "DocIRDocument":
        """Load from a ``.docir.json`` file."""
        with file_path.open("r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


# ----------------------------------------------------------------------------
# Tree helpers
# ----------------------------------------------------------------------------
def build_page_map(root: Optional[DocIRNode]) -> Dict[str, Dict[str, Any]]:
    """Derive ``page_map`` (page -> {node_ids, render_ref}) from the tree.

    ``render_ref`` is taken from the first visual node on that page (if any).
    """
    page_map: Dict[str, Dict[str, Any]] = {}
    if root is None:
        return page_map
    stack = [root]
    while stack:
        node = stack.pop()
        if node.loc and node.loc.page is not None:
            key = str(node.loc.page)
            entry = page_map.setdefault(key, {"node_ids": [], "render_ref": None})
            if node.id not in entry["node_ids"]:
                entry["node_ids"].append(node.id)
            if entry["render_ref"] is None and node.vision and node.vision.render_ref:
                entry["render_ref"] = node.vision.render_ref
        stack.extend(node.children)
    return page_map


def collect_vision_node_ids(root: Optional[DocIRNode]) -> List[str]:
    """Collect ids of all figure_anchor nodes (the vision_nodes list)."""
    ids: List[str] = []
    if root is None:
        return ids
    stack = [root]
    while stack:
        node = stack.pop()
        if node.is_visual():
            ids.append(node.id)
        stack.extend(node.children)
    return ids


def _uri_scheme(uri: str) -> Optional[str]:
    """Extract the scheme from a ``scheme://...`` URI (lowercased)."""
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.\-]*?)://", uri)
    return m.group(1).lower() if m else None


def make_node_id(doc_name: str, *path_parts: str) -> str:
    """Build a stable ``docir://<doc_name>/<path>`` node id.

    Short docs with no hierarchy may pass a paragraph index: ``make_node_id(name, '#p3')``.
    """
    doc_slug = _sanitize_slug(doc_name)
    if not path_parts:
        return f"docir://{doc_slug}"
    path = "/".join(_sanitize_slug(p, keep_hash=True) for p in path_parts if p)
    return f"docir://{doc_slug}/{path}" if path else f"docir://{doc_slug}"


_SAFE_RE = re.compile(r"[^\w\-/#]+")


def _sanitize_slug(s: str, keep_hash: bool = False) -> str:
    """Slugify a path segment for use in a docir:// URI."""
    if not s:
        return ""
    pattern = _SAFE_RE if keep_hash else re.compile(r"[^\w\-/]+")
    return pattern.sub("-", s).strip("-/") or "node"


def make_doc_id(doc_name: str) -> str:
    """Deterministic doc_id from doc_name (``<slug>_<sha8>``)."""
    slug = _sanitize_slug(doc_name)
    digest = hashlib.sha256(doc_name.encode("utf-8")).hexdigest()[:8]
    return f"{slug}_{digest}"


# DocIRBuilder + create_docir_from_markdown live in openkb.docir_builder
# (split by responsibility: data model here, construction there).
from openkb.docir_builder import DocIRBuilder, create_docir_from_markdown  # noqa: E402

__all__ = [
    "DOCIR_VERSION",
    # kind constants
    "KIND_DOCUMENT",
    "KIND_SECTION",
    "KIND_PARAGRAPH",
    "KIND_LIST_ITEM",
    "KIND_TABLE",
    "KIND_FIGURE_ANCHOR",
    "KIND_PAGE_MARKER",
    # extractor constants
    "EXTRACTOR_PAGEINDEX_TREE",
    "EXTRACTOR_PYMUPDF_TEXT",
    "EXTRACTOR_MARKITDOWN",
    "EXTRACTOR_MD_PARSER",
    "EXTRACTOR_OCR",
    "EXTRACTOR_PDF_FIGURE_DETECT",
    "EXTRACTOR_LLM_SUMMARY",
    # vision type constants
    "VISION_CHART",
    "VISION_SIGNATURE",
    "VISION_PHOTO",
    "VISION_HANDWRITING",
    "VISION_TABLE_COMPLEX",
    # scheme constants
    "SCHEME_DOCIR",
    "SCHEME_LAW",
    "SCHEME_CASE",
    # dataclasses
    "DocIRLoc",
    "DocIRProvenance",
    "DocIRAnchors",
    "DocIRVision",
    "DocIRNode",
    "DocIRSource",
    "DocIRFormat",
    "DocIRDocument",
    "DocIRBuilder",
    # helpers
    "make_node_id",
    "make_doc_id",
    "build_page_map",
    "collect_vision_node_ids",
    "create_docir_from_markdown",
]
