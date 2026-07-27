"""Tests for openkb.legal.graph_extract - heuristic graph extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from openkb.legal.graph_extract import extract_doc_graph, extract_graph_from_wiki, graph_stats
from openkb.legal.schema import RelationType


def _write_page(kb: Path, rel: str, fm: str = "", body: str = "") -> None:
    p = kb / "wiki" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{fm}---\n\n{body}\n", encoding="utf-8")


@pytest.fixture
def kb_with_graph(tmp_path: Path) -> Path:
    (tmp_path / ".openkb").mkdir()
    _write_page(
        tmp_path,
        "entities/民法典第577条.md",
        fm='type: "Statute"\ndescription: "民法典第577条"\nauthority_level: "statute"\n',
        body="违约责任条款。\n",
    )
    _write_page(
        tmp_path,
        "entities/张某案.md",
        fm='type: "Case"\ndescription: "张某案"\n',
        body="本案适用[[entities/民法典第577条]]。\n",
    )
    _write_page(
        tmp_path,
        "entities/王某案.md",
        fm='type: "Case"\ndescription: "王某案"\n',
        body="类案[[entities/张某案]]，适用[[entities/民法典第577条]]。\n",
    )
    _write_page(
        tmp_path,
        "concepts/违约责任.md",
        fm='type: "Concept"\ndescription: "违约责任原则"\n',
        body="参见[[entities/民法典第577条]]与[[entities/张某案]]。\n",
    )
    return tmp_path


class TestExtraction:
    def test_extracts_nodes_from_pages(self, kb_with_graph):
        extract_graph_from_wiki(kb_with_graph)
        stats = graph_stats(kb_with_graph)
        assert stats["node_count"] == 4
        assert "statute" in stats["node_types"]
        assert stats["node_types"]["case"] == 2

    def test_extracts_wikilink_edges(self, kb_with_graph):
        extract_graph_from_wiki(kb_with_graph)
        stats = graph_stats(kb_with_graph)
        assert stats["edge_count"] >= 4  # case->statute, case->case, concept->*

    def test_relation_types_heuristic(self, kb_with_graph):
        g = extract_graph_from_wiki(kb_with_graph)
        case = g.find_node("张某案", "case")
        statute = g.find_node("民法典第577条", "statute")
        cited = g.find_related(case.node_id, RelationType.CITES)
        assert any(n.node_id == statute.node_id for n, _ in cited)

    def test_authority_level_mapped(self, kb_with_graph):
        g = extract_graph_from_wiki(kb_with_graph)
        statute = g.find_node("民法典第577条", "statute")
        assert statute.authority_level is not None

    def test_idempotent(self, kb_with_graph):
        extract_graph_from_wiki(kb_with_graph)
        before = graph_stats(kb_with_graph)
        extract_graph_from_wiki(kb_with_graph)
        after = graph_stats(kb_with_graph)
        assert before == after

    def test_extract_doc_graph_is_full_rescan(self, kb_with_graph):
        extract_doc_graph(kb_with_graph, "case001")
        assert graph_stats(kb_with_graph)["node_count"] == 4

    def test_empty_wiki(self, tmp_path):
        (tmp_path / ".openkb").mkdir()
        (tmp_path / "wiki").mkdir()
        extract_graph_from_wiki(tmp_path)
        assert graph_stats(tmp_path)["node_count"] == 0

    def test_self_link_ignored(self, tmp_path):
        (tmp_path / ".openkb").mkdir()
        _write_page(
            tmp_path,
            "entities/x.md",
            fm='type: "Statute"\ndescription: "x"\n',
            body="self [[entities/x]]\n",
        )
        extract_graph_from_wiki(tmp_path)
        assert graph_stats(tmp_path)["edge_count"] == 0

    def test_broken_wikilink_ignored(self, tmp_path):
        (tmp_path / ".openkb").mkdir()
        _write_page(
            tmp_path,
            "entities/x.md",
            fm='type: "Statute"\ndescription: "x"\n',
            body="refs [[entities/nonexistent]]\n",
        )
        extract_graph_from_wiki(tmp_path)
        assert graph_stats(tmp_path)["edge_count"] == 0
