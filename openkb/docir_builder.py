"""DocIR builders - stream-style construction of a DocIR tree.

Split from :mod:`openkb.docir` (the data model) by responsibility: the
dataclasses live in ``docir.py``; the builders that assemble them live here.
``DocIRBuilder`` adds nodes flat with an optional ``parent_id`` and assembles
the recursive tree on ``build()``; :func:`create_docir_from_markdown` is the
shallow-tree Markdown parser shared by ``converter.py`` and tests. Long PDFs
build their deep tree directly in ``indexer.py`` via ``DocIRBuilder``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from openkb.docir import (
    EXTRACTOR_MD_PARSER,
    EXTRACTOR_PDF_FIGURE_DETECT,
    KIND_DOCUMENT,
    KIND_FIGURE_ANCHOR,
    KIND_PARAGRAPH,
    KIND_SECTION,
    VISION_PHOTO,
    DocIRAnchors,
    DocIRDocument,
    DocIRFormat,
    DocIRLoc,
    DocIRNode,
    DocIRProvenance,
    DocIRSource,
    DocIRVision,
    _sanitize_slug,
    make_doc_id,
    make_node_id,
)


class DocIRBuilder:
    """Stream-style builder for a DocIR document.

    Nodes are added flat with an optional ``parent_id``; the tree is assembled
    on ``build()`` and indices are rebuilt. Use :meth:`add_node` for content
    nodes and :meth:`add_visual_node` for figure_anchor nodes (registers a
    render pointer + text anchor, NO analysis).
    """

    def __init__(
        self,
        doc_name: str,
        *,
        doc_id: Optional[str] = None,
        input_type: str = "txt",
        converter: str = EXTRACTOR_MD_PARSER,
        converter_version: str = "",
        origin_type: str = "file",
        origin_uri: str = "",
        content_hash: Optional[str] = None,
        title: Optional[str] = None,
    ) -> None:
        self.doc_name = doc_name
        self.doc_id = doc_id or make_doc_id(doc_name)
        self.title = title
        self.source = DocIRSource(
            origin_type=origin_type,
            origin_uri=origin_uri,
            content_hash=content_hash,
        )
        self.format = DocIRFormat(
            input_type=input_type,
            converter=converter,
            converter_version=converter_version,
        )
        self._nodes: List[DocIRNode] = []
        self._parent_ids: List[Optional[str]] = []
        self._extensions: Dict[str, Any] = {}
        self._root_id: Optional[str] = None
        self._counter = 0

    # -- metadata -----------------------------------------------------------

    def set_title(self, title: str) -> "DocIRBuilder":
        self.title = title
        return self

    def set_input_meta(self, **meta: Any) -> "DocIRBuilder":
        self.format.input_meta.update(meta)
        return self

    def set_extension(self, name: str, data: Dict[str, Any]) -> "DocIRBuilder":
        self._extensions[name] = data
        return self

    def set_sync_source(self, sync_source: str) -> "DocIRBuilder":
        self.source.sync_source = sync_source
        return self

    def set_ingested_at(self, iso_ts: str) -> "DocIRBuilder":
        self.source.ingested_at = iso_ts
        return self

    # -- node construction --------------------------------------------------

    def _next_id(self, kind: str, title: Optional[str]) -> str:
        self._counter += 1
        slug = _sanitize_slug(title or f"{kind}-{self._counter}")
        return make_node_id(self.doc_name, slug, str(self._counter))

    def add_node(
        self,
        kind: str = KIND_PARAGRAPH,
        text: str = "",
        *,
        title: Optional[str] = None,
        page: Optional[int] = None,
        char_start: Optional[int] = None,
        char_end: Optional[int] = None,
        bbox: Optional[List[float]] = None,
        parent_id: Optional[str] = None,
        node_id: Optional[str] = None,
        extractor: str = EXTRACTOR_MD_PARSER,
        confidence: float = 1.0,
        verified: bool = False,
        anchors: Optional[DocIRAnchors] = None,
    ) -> str:
        """Add a content node. Returns its id."""
        nid = node_id or self._next_id(kind, title)
        loc = DocIRLoc(page=page, char_start=char_start, char_end=char_end, bbox=bbox)
        if anchors is None:
            anchors = DocIRAnchors(default=nid)
        node = DocIRNode(
            id=nid,
            kind=kind,
            title=title,
            text=text,
            loc=loc,
            provenance=DocIRProvenance(
                extractor=extractor, confidence=confidence, verified=verified
            ),
            anchors=anchors,
        )
        self._nodes.append(node)
        self._parent_ids.append(parent_id)
        if parent_id is None and self._root_id is None:
            self._root_id = nid
        return nid

    def add_visual_node(
        self,
        *,
        page: Optional[int] = None,
        visual_type: str = VISION_PHOTO,
        text_anchor: Optional[str] = None,
        render_ref: Optional[str] = None,
        title: Optional[str] = None,
        bbox: Optional[List[float]] = None,
        parent_id: Optional[str] = None,
        node_id: Optional[str] = None,
        extractor: str = EXTRACTOR_PDF_FIGURE_DETECT,
        confidence: float = 1.0,
    ) -> str:
        """Register a visual node (figure_anchor) - NO pre-analysis.

        Records page, bbox, type, surrounding text anchor, and render pointer
        only. Analysis is on-demand at query time.
        """
        nid = node_id or self._next_id(KIND_FIGURE_ANCHOR, title)
        loc = DocIRLoc(page=page, bbox=bbox)
        node = DocIRNode(
            id=nid,
            kind=KIND_FIGURE_ANCHOR,
            title=title,
            text="",
            loc=loc,
            provenance=DocIRProvenance(extractor=extractor, confidence=confidence),
            anchors=DocIRAnchors(default=nid),
            vision=DocIRVision(type=visual_type, text_anchor=text_anchor, render_ref=render_ref),
        )
        self._nodes.append(node)
        self._parent_ids.append(parent_id)
        return nid

    # -- assembly -----------------------------------------------------------

    def build(self) -> DocIRDocument:
        """Assemble the tree, rebuild indices, and return the document."""
        if not self._nodes:
            root = DocIRNode(id=make_node_id(self.doc_name), kind=KIND_DOCUMENT, title=self.title)
        else:
            by_id: Dict[str, DocIRNode] = {}
            for node in self._nodes:
                node.children = []
                by_id[node.id] = node
            root_id = self._root_id or self._nodes[0].id
            root = by_id[root_id]
            attached = {root_id}
            for node, pid in zip(self._nodes, self._parent_ids):
                if pid is not None and pid in by_id:
                    by_id[pid].children.append(node)
                    attached.add(node.id)
            # Attach orphan nodes (e.g. a visual node registered without a
            # parent) under the root so they remain reachable from the tree.
            for node in self._nodes:
                if node.id not in attached:
                    root.children.append(node)
        doc = DocIRDocument(
            doc_id=self.doc_id,
            doc_name=self.doc_name,
            title=self.title,
            source=self.source,
            format=self.format,
            root=root,
            extensions=dict(self._extensions),
        )
        doc.rebuild_indices()
        return doc


def create_docir_from_markdown(
    markdown: str,
    doc_name: str,
    *,
    input_type: str = "md",
    converter: str = EXTRACTOR_MD_PARSER,
    origin_uri: str = "",
    content_hash: Optional[str] = None,
    title: Optional[str] = None,
) -> DocIRDocument:
    """Build a shallow DocIR from Markdown (root -> sections/paragraphs).

    Heading level maps to node depth: ``#`` -> section (root child), ``##`` ->
    nested section, body lines -> paragraphs under the current heading. Long
    PDFs use the deep-tree path (``indexer.py`` + pageindex-tree extractor).
    """
    builder = DocIRBuilder(
        doc_name,
        input_type=input_type,
        converter=converter,
        origin_uri=origin_uri,
        content_hash=content_hash,
        title=title,
    )
    heading_stack: List[Tuple[int, str]] = []  # (level, node_id)
    root_id = builder.add_node(
        kind=KIND_DOCUMENT, title=title or doc_name, extractor=converter, confidence=1.0
    )
    char = 0
    for line in markdown.split("\n"):
        char += len(line) + 1
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            level = len(m.group(1))
            heading = m.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            parent = heading_stack[-1][1] if heading_stack else root_id
            nid = builder.add_node(
                kind=KIND_SECTION,
                text="",
                title=heading,
                char_start=char - len(line) - 1,
                char_end=char,
                parent_id=parent,
                extractor=converter,
            )
            heading_stack.append((level, nid))
        elif line.strip():
            parent = heading_stack[-1][1] if heading_stack else root_id
            builder.add_node(
                kind=KIND_PARAGRAPH,
                text=line,
                char_start=char - len(line) - 1,
                char_end=char,
                parent_id=parent,
                extractor=converter,
            )
    return builder.build()


__all__ = ["DocIRBuilder", "create_docir_from_markdown"]
