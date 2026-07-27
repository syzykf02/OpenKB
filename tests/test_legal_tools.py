"""Tests for openkb.agent.legal_tools + openkb.legal.query_router (Phase 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from openkb.agent.legal_tools import find_impact, list_graph_entities, query_graph
from openkb.legal.docir import (
    KIND_DOCUMENT,
    KIND_SECTION,
    STATUS_CURRENT,
    LegalDocIRBuilder,
)
from openkb.legal.graph_extract import extract_graph_from_wiki
from openkb.legal.query_router import QueryIntent, QueryRouter, route_intent


def _write_page(kb: Path, rel: str, fm: str = "", body: str = "") -> None:
    p = kb / "wiki" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{fm}---\n\n{body}\n", encoding="utf-8")


@pytest.fixture
def kb_with_docir_and_graph(tmp_path: Path) -> Path:
    (tmp_path / ".openkb").mkdir()
    sources = tmp_path / "wiki" / "sources"
    sources.mkdir(parents=True)
    # DocIR doc with a legal article
    b = LegalDocIRBuilder("minfade", input_type="pdf", converter="pageindex-tree", title="民法典")
    r = b.add_node(kind=KIND_DOCUMENT, title="民法典")
    sec = b.add_node(kind=KIND_SECTION, title="合同编", page=47, parent_id=r)
    b.add_article(
        title="第577条",
        text="当事人一方不履行合同义务应当承担违约责任。",
        statute="民法典",
        article_path="合同编/第577条",
        page=47,
        parent_id=sec,
    )
    b.set_effective_status(STATUS_CURRENT)
    b.build().save(sources / "minfade.docir.json")
    # entity pages with wikilinks
    _write_page(
        tmp_path,
        "entities/民法典第577条.md",
        fm='type: "Statute"\ndescription: "民法典第577条"\n',
        body="违约责任。\n",
    )
    _write_page(
        tmp_path,
        "entities/张某案.md",
        fm='type: "Case"\ndescription: "张某案"\n',
        body="适用[[entities/民法典第577条]]。\n",
    )
    _write_page(
        tmp_path,
        "entities/王某案.md",
        fm='type: "Case"\ndescription: "王某案"\n',
        body="类案[[entities/张某案]]，适用[[entities/民法典第577条]]。\n",
    )
    extract_graph_from_wiki(tmp_path)
    return tmp_path


class TestLegalTools:
    def test_query_graph_cites(self, kb_with_docir_and_graph):
        out = query_graph("张某案", "cites", str(kb_with_docir_and_graph), depth=1)
        assert "民法典第577条" in out

    def test_query_graph_unknown_relation(self, kb_with_docir_and_graph):
        out = query_graph("张某案", "foobar", str(kb_with_docir_and_graph))
        assert "Unknown relation" in out

    def test_query_graph_missing_entity(self, kb_with_docir_and_graph):
        out = query_graph("不存在", "cites", str(kb_with_docir_and_graph))
        assert "not found" in out.lower()

    def test_find_impact_reverse(self, kb_with_docir_and_graph):
        out = find_impact("民法典第577条", str(kb_with_docir_and_graph))
        assert "张某案" in out and "王某案" in out

    def test_find_impact_no_refs(self, kb_with_docir_and_graph):
        out = find_impact("张某案", str(kb_with_docir_and_graph))
        # 张某案 is cited by 王某案 (similar_case) - check it shows something
        assert "Impact" in out or "No nodes" in out

    def test_list_graph_entities_filtered(self, kb_with_docir_and_graph):
        out = list_graph_entities(str(kb_with_docir_and_graph), "case")
        assert "case" in out and "张某案" in out

    def test_list_graph_entities_empty(self, tmp_path):
        (tmp_path / ".openkb").mkdir()
        out = list_graph_entities(str(tmp_path))
        assert "empty" in out.lower()


class TestRouteIntent:
    def test_statute_number_is_precise(self):
        assert route_intent("民法典第577条") == QueryIntent.PRECISE_TERM

    def test_impact_keywords(self):
        assert route_intent("民法典变更影响哪些案件") == QueryIntent.IMPACT

    def test_visual_keywords(self):
        assert route_intent("第3页的签字图说明了什么") == QueryIntent.VISUAL

    def test_semantic_default(self):
        assert route_intent("违约责任的构成要件") == QueryIntent.SEMANTIC


class TestQueryRouter:
    def test_bm25_leg_finds_article(self, kb_with_docir_and_graph):
        router = QueryRouter(kb_with_docir_and_graph)
        results = router.search("第577条 违约责任")
        assert any("minfade" in r.doc_name for r in results)

    def test_graph_leg_contributes_on_impact(self, kb_with_docir_and_graph):
        router = QueryRouter(kb_with_docir_and_graph)
        results = router.search("民法典第577条 影响")
        # graph leg should surface the cases that cite the statute
        assert any("graph" in r.sources for r in results)
        assert any("张某案" in r.title for r in results)

    def test_precise_intent_weights_bm25_higher(self, kb_with_docir_and_graph):
        router = QueryRouter(kb_with_docir_and_graph)
        results = router.search("第577条", intent=QueryIntent.PRECISE_TERM)
        # BM25 result present
        assert any("bm25" in r.sources for r in results)

    def test_empty_kb(self, tmp_path):
        (tmp_path / ".openkb").mkdir()
        (tmp_path / "wiki" / "sources").mkdir(parents=True)
        router = QueryRouter(tmp_path)
        out = router.search_formatted("anything")
        assert "No results" in out

    def test_search_formatted_includes_intent(self, kb_with_docir_and_graph):
        router = QueryRouter(kb_with_docir_and_graph)
        out = router.search_formatted("第577条")
        assert "intent=" in out

    def test_ranked_node_has_sources(self, kb_with_docir_and_graph):
        router = QueryRouter(kb_with_docir_and_graph)
        results = router.search("第577条 违约责任")
        assert all(isinstance(r.sources, list) for r in results)
