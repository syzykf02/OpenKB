"""Tests for openkb.agent.docir_tools - DocIR read/search/render tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from openkb.agent.docir_tools import read_node, render_page, search_docir
from openkb.docir import KIND_DOCUMENT, KIND_SECTION, DocIRBuilder
from openkb.legal.docir import (
    KIND_ARTICLE,
    LegalDocIRBuilder,
    STATUS_CURRENT,
    law_uri,
)


@pytest.fixture
def kb_with_docir(tmp_path: Path) -> Path:
    """A wiki_root with two DocIR docs (a statute + a case)."""
    wiki = tmp_path / "wiki"
    sources = wiki / "sources"
    sources.mkdir(parents=True)
    (tmp_path / "raw").mkdir()

    # statute doc with a legal article
    b = LegalDocIRBuilder("minfade", input_type="pdf", converter="pageindex-tree", title="民法典")
    root = b.add_node(kind=KIND_DOCUMENT, title="民法典")
    sec = b.add_node(kind=KIND_SECTION, title="合同编", page=47, parent_id=root)
    b.add_article(
        title="第577条",
        text="当事人一方不履行合同义务应当承担违约责任。",
        statute="民法典", article_path="合同编/第577条", page=47, parent_id=sec,
    )
    b.add_visual_node(page=47, visual_type="signature", text_anchor="签字处", render_ref="sources/images/minfade/p47.png", parent_id=sec)
    b.set_effective_status(STATUS_CURRENT)
    b.build().save(sources / "minfade.docir.json")

    # case doc
    b2 = LegalDocIRBuilder("case001", input_type="pdf", converter="pageindex-tree", title="张某案")
    r2 = b2.add_node(kind=KIND_DOCUMENT, title="张某案")
    b2.add_node(kind=KIND_SECTION, title="判决理由", text="本案适用民法典第577条。", page=5, parent_id=r2)
    b2.build().save(sources / "case001.docir.json")

    return wiki


class TestReadNode:
    def test_read_by_legal_uri(self, kb_with_docir):
        out = read_node("law://民法典/合同编/第577条", str(kb_with_docir))
        assert "第577条" in out
        assert "legal_anchor: law://民法典/合同编/第577条" in out
        assert "page=47" in out

    def test_read_by_docir_id(self, kb_with_docir):
        # Find the article node id via the loaded doc
        from openkb.docir import DocIRDocument

        doc = DocIRDocument.load(kb_with_docir / "sources" / "minfade.docir.json")
        art = [n for n in doc.node_table().values() if n.kind == KIND_ARTICLE][0]
        out = read_node(art.id, str(kb_with_docir))
        assert "违约责任" in out

    def test_not_found(self, kb_with_docir):
        assert "not found" in read_node("law://不存在/第1条", str(kb_with_docir)).lower()

    def test_empty_wiki(self, tmp_path):
        (tmp_path / "sources").mkdir(parents=True)
        assert "not found" in read_node("docir://x", str(tmp_path)).lower()


class TestSearchDocir:
    def test_bm25_finds_statute(self, kb_with_docir):
        res = search_docir("违约责任 577", str(kb_with_docir))
        assert "minfade" in res

    def test_bm25_finds_case(self, kb_with_docir):
        res = search_docir("判决 民法典", str(kb_with_docir))
        assert "case001" in res

    def test_no_results(self, kb_with_docir):
        assert "No results" in search_docir("zzznonexistent", str(kb_with_docir))

    def test_empty_wiki(self, tmp_path):
        (tmp_path / "sources").mkdir(parents=True)
        assert "No results" in search_docir("anything", str(tmp_path))


class TestRenderPage:
    def test_text_first_when_no_visual(self, kb_with_docir, tmp_path):
        # case001 page 5 has no visual nodes
        r = render_page("case001", "5", str(tmp_path))
        assert r["type"] == "text"
        assert "no registered visual" in r["text"]

    def test_visual_node_without_raw_pdf_returns_render_ref(self, kb_with_docir, tmp_path):
        # minfade page 47 has a visual node; no raw PDF present
        r = render_page("minfade", "47", str(tmp_path))
        assert r["type"] == "text"
        assert "visual node" in r["text"]
        assert r["visual_nodes"][0]["render_ref"] == "sources/images/minfade/p47.png"

    def test_no_docir(self, tmp_path):
        (tmp_path / "wiki" / "sources").mkdir(parents=True)
        r = render_page("nope", "1", str(tmp_path))
        assert r["type"] == "text" and "not found" in r["text"]

    def test_invalid_page(self, kb_with_docir, tmp_path):
        r = render_page("minfade", "abc", str(tmp_path))
        assert r["type"] == "text" and "Invalid" in r["text"]
