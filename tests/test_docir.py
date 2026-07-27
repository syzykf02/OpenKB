"""Tests for openkb.docir - the domain-agnostic DocIR core (spec/docir-format.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openkb.docir import (
    DOCIR_VERSION,
    DocIRAnchors,
    DocIRBuilder,
    DocIRDocument,
    DocIRLoc,
    DocIRNode,
    DocIRProvenance,
    DocIRVision,
    EXTRACTOR_MD_PARSER,
    EXTRACTOR_PAGEINDEX_TREE,
    EXTRACTOR_PDF_FIGURE_DETECT,
    KIND_DOCUMENT,
    KIND_FIGURE_ANCHOR,
    KIND_PARAGRAPH,
    KIND_SECTION,
    SCHEME_CASE,
    SCHEME_DOCIR,
    SCHEME_LAW,
    VISION_SIGNATURE,
    build_page_map,
    collect_vision_node_ids,
    create_docir_from_markdown,
    make_doc_id,
    make_node_id,
)


class TestNodeDataclasses:
    def test_loc_omits_none_fields(self):
        d = DocIRLoc(page=47).to_dict()
        assert d == {"page": 47}

    def test_loc_from_empty_dict_is_none(self):
        assert DocIRLoc.from_dict({}) is None
        assert DocIRLoc.from_dict(None) is None

    def test_provenance_defaults(self):
        p = DocIRProvenance()
        assert p.extractor == EXTRACTOR_MD_PARSER
        assert p.confidence == 1.0
        assert p.verified is False

    def test_anchors_by_scheme(self):
        a = DocIRAnchors(default="docir://d/x", legal="law://s/a")
        assert a.by_scheme(SCHEME_DOCIR) == "docir://d/x"
        assert a.by_scheme(SCHEME_LAW) == "law://s/a"
        assert a.by_scheme(SCHEME_CASE) == "law://s/a"
        assert a.by_scheme("unknown") is None

    def test_vision_round_trip(self):
        v = DocIRVision(type=VISION_SIGNATURE, text_anchor="签字处", render_ref="render://d/p1.png")
        d = v.to_dict()
        assert d["type"] == "signature" and d["text_anchor"] == "签字处"
        v2 = DocIRVision.from_dict(d)
        assert v2.type == VISION_SIGNATURE and v2.render_ref == "render://d/p1.png"


class TestBuilderAndTree:
    def test_build_shallow_tree_and_indices(self):
        b = DocIRBuilder("doc1", input_type="md", converter=EXTRACTOR_MD_PARSER)
        root = b.add_node(kind=KIND_DOCUMENT, title="doc1")
        sec = b.add_node(kind=KIND_SECTION, title="S1", page=1, parent_id=root)
        b.add_node(kind=KIND_PARAGRAPH, text="hello", page=1, parent_id=sec)
        doc = b.build()
        assert doc.docir_version == DOCIR_VERSION
        assert doc.doc_id.startswith("doc1_")
        table = doc.node_table()
        assert len(table) == 3
        # root is the document node
        assert doc.root.kind == KIND_DOCUMENT
        # page_map derived
        assert "1" in doc.page_map
        assert len(doc.page_map["1"]["node_ids"]) == 2  # section + paragraph

    def test_visual_node_attached_and_indexed(self):
        b = DocIRBuilder("d", input_type="pdf")
        root = b.add_node(kind=KIND_DOCUMENT, title="d")
        b.add_node(kind=KIND_SECTION, title="S", page=5, parent_id=root)
        vid = b.add_visual_node(page=5, visual_type=VISION_SIGNATURE, text_anchor="签字", render_ref="render://d/p5.png")
        doc = b.build()
        assert doc.vision_nodes == [vid]
        assert len(doc.get_visual_nodes()) == 1
        assert doc.has_visual_content()
        # page_map picks up the render_ref
        assert doc.page_map["5"]["render_ref"] == "render://d/p5.png"

    def test_orphan_visual_node_attaches_to_root(self):
        """A visual node added without a parent stays reachable under root."""
        b = DocIRBuilder("d", input_type="pdf")
        b.add_node(kind=KIND_DOCUMENT, title="d")
        vid = b.add_visual_node(page=2, visual_type=VISION_SIGNATURE)
        doc = b.build()
        assert vid in doc.node_table()
        assert doc.vision_nodes == [vid]

    def test_legal_anchor_resolves(self):
        b = DocIRBuilder("m", input_type="md")
        root = b.add_node(kind=KIND_DOCUMENT, title="m")
        nid = b.add_node(
            kind=KIND_SECTION, title="第577条", text="违约责任", page=47, parent_id=root,
            anchors=DocIRAnchors(default="docir://m/577", legal="law://民法典/第577条"),
        )
        doc = b.build()
        assert doc.resolve_uri("law://民法典/第577条") is not None
        assert doc.resolve_uri("docir://m/577") is not None
        assert doc.resolve_uri("case://foo") is None

    def test_get_nodes_by_page(self):
        b = DocIRBuilder("d", input_type="md")
        root = b.add_node(kind=KIND_DOCUMENT, title="d")
        b.add_node(kind=KIND_PARAGRAPH, text="a", page=1, parent_id=root)
        b.add_node(kind=KIND_PARAGRAPH, text="b", page=2, parent_id=root)
        doc = b.build()
        assert len(doc.get_nodes_by_page(1)) == 1
        assert len(doc.get_nodes_by_page(2)) == 1
        assert doc.get_nodes_by_page(99) == []


class TestSerialization:
    def test_round_trip(self, tmp_path: Path):
        b = DocIRBuilder("doc1", input_type="pdf", converter=EXTRACTOR_PAGEINDEX_TREE)
        root = b.add_node(kind=KIND_DOCUMENT, title="doc1")
        sec = b.add_node(kind=KIND_SECTION, title="S1", page=1, parent_id=root)
        b.add_node(kind=KIND_PARAGRAPH, text="text", page=1, parent_id=sec)
        b.add_visual_node(page=1, visual_type=VISION_SIGNATURE, render_ref="render://doc1/p1.png")
        doc = b.build()
        p = tmp_path / "doc1.docir.json"
        doc.save(p)
        loaded = DocIRDocument.load(p)
        assert loaded.doc_id == doc.doc_id
        assert loaded.format.converter == EXTRACTOR_PAGEINDEX_TREE
        assert len(loaded.node_table()) == 4  # root + section + paragraph + visual
        assert loaded.vision_nodes == doc.vision_nodes
        assert loaded.page_map == doc.page_map

    def test_save_uses_atomic_write(self, tmp_path: Path, monkeypatch):
        """save() should route through openkb.locks.atomic_write_text when available."""
        called = []
        import openkb.locks as locks

        def fake_write(path, text):
            called.append(path)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(text, encoding="utf-8")

        monkeypatch.setattr(locks, "atomic_write_text", fake_write)
        doc = DocIRBuilder("d", input_type="md").build()
        doc.save(tmp_path / "d.docir.json")
        assert called and called[0].name == "d.docir.json"


class TestMarkdownHelper:
    def test_heading_levels_build_tree(self):
        md = "# Title\npara A\n## Sub\npara B\n"
        doc = create_docir_from_markdown(md, "test", input_type="md")
        assert doc.root.kind == KIND_DOCUMENT
        table = doc.node_table()
        # root + H1 section + 2 paragraphs + H2 section = 5
        assert len(table) == 5
        # H2 section's parent is H1 section
        h2 = [n for n in table.values() if n.title == "Sub"][0]
        h1 = [n for n in table.values() if n.title == "Title"][0]
        # H2 should be a descendant of H1 (child of root's child)
        assert h2 in h1.children


class TestHelpers:
    def test_make_node_id(self):
        assert make_node_id("民法典", "合同编", "第577条").startswith("docir://民法典/")

    def test_make_doc_id_stable(self):
        assert make_doc_id("doc") == make_doc_id("doc")
        assert make_doc_id("a") != make_doc_id("b")

    def test_build_page_map_empty(self):
        assert build_page_map(None) == {}

    def test_collect_vision_node_ids(self):
        b = DocIRBuilder("d", input_type="md")
        root = b.add_node(kind=KIND_DOCUMENT, title="d")
        v1 = b.add_visual_node(page=1)
        v2 = b.add_visual_node(page=2, parent_id=root)
        doc = b.build()
        ids = collect_vision_node_ids(doc.root)
        assert set(ids) == {v1, v2}
