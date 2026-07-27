"""Tests for openkb.visual.analyzer + openkb.legal.ingest_hooks (Phase 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from openkb.docir import KIND_DOCUMENT, KIND_SECTION, VISION_CHART, DocIRBuilder
from openkb.legal.ingest_hooks import run_ingest_hooks, run_maintenance_hooks
from openkb.visual.analyzer import LOW_CONFIDENCE_THRESHOLD, VisionAnalyzer


def _make_pdf(path: Path, text: str = "page") -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 72), text)
    doc.save(str(path))
    doc.close()


@pytest.fixture
def kb_with_visual_doc(tmp_path: Path) -> Path:
    (tmp_path / ".openkb").mkdir()
    (tmp_path / "raw").mkdir()
    sources = tmp_path / "wiki" / "sources"
    sources.mkdir(parents=True)
    b = DocIRBuilder("evidence001", input_type="pdf", converter="pageindex-tree", title="流水证据")
    r = b.add_node(kind=KIND_DOCUMENT, title="流水证据")
    b.add_node(kind=KIND_SECTION, title="图表", text="转账87万(见下图)", page=1, parent_id=r)
    b.add_visual_node(
        page=1,
        visual_type=VISION_CHART,
        text_anchor="转账87万",
        render_ref="sources/images/evidence001/p1.png",
        parent_id=r,
    )
    b.build().save(sources / "evidence001.docir.json")
    _make_pdf(tmp_path / "raw" / "evidence001.pdf", "bank flow chart")
    return tmp_path


def _mock_llm(answer: str = "87万", confidence: float = 0.88):
    def _call(image_url, question, model):
        return {"answer": answer, "confidence": confidence, "note": "mock"}

    return _call


class TestVisionAnalyzer:
    def test_analyze_page_with_visual(self, kb_with_visual_doc):
        analyzer = VisionAnalyzer(kb_with_visual_doc, llm_call=_mock_llm())
        res = analyzer.analyze_page("evidence001", 1, "流水图金额?")
        assert res.error is None
        assert "87万" in res.answer
        assert res.confidence == 0.88

    def test_text_first_no_visual(self, tmp_path):
        (tmp_path / ".openkb").mkdir()
        (tmp_path / "wiki" / "sources").mkdir(parents=True)
        b = DocIRBuilder("plain", input_type="md")
        r = b.add_node(kind=KIND_DOCUMENT, title="plain")
        b.add_node(kind=KIND_SECTION, title="s", text="text only", page=1, parent_id=r)
        b.build().save(tmp_path / "wiki" / "sources" / "plain.docir.json")
        analyzer = VisionAnalyzer(tmp_path, llm_call=_mock_llm())
        res = analyzer.analyze_page("plain", 1, "q")
        assert res.error == "no_visual_content"

    def test_low_confidence_degradation(self, kb_with_visual_doc):
        analyzer = VisionAnalyzer(kb_with_visual_doc, llm_call=_mock_llm(confidence=0.3))
        res = analyzer.analyze_page("evidence001", 1, "q")
        assert res.confidence < LOW_CONFIDENCE_THRESHOLD
        assert "人工看图" in res.note

    def test_analyze_node_caches(self, kb_with_visual_doc):
        # find the visual node id
        from openkb.docir import DocIRDocument

        doc = DocIRDocument.load(kb_with_visual_doc / "wiki" / "sources" / "evidence001.docir.json")
        vis = [n for n in doc.node_table().values() if n.is_visual()][0]
        analyzer = VisionAnalyzer(kb_with_visual_doc, llm_call=_mock_llm())
        res = analyzer.analyze_node(vis.id, "金额?")
        assert res.error is None
        cached = analyzer.get_cached_analysis(vis.id)
        assert cached is not None and cached["confidence"] == 0.88

    def test_analyze_node_missing(self, tmp_path):
        (tmp_path / ".openkb").mkdir()
        (tmp_path / "wiki" / "sources").mkdir(parents=True)
        analyzer = VisionAnalyzer(tmp_path, llm_call=_mock_llm())
        res = analyzer.analyze_node("docir://nonexistent", "q")
        assert res.error == "visual node not found"

    def test_llm_failure_degrades(self, kb_with_visual_doc):
        def failing_llm(image_url, question, model):
            raise RuntimeError("model down")

        analyzer = VisionAnalyzer(kb_with_visual_doc, llm_call=failing_llm)
        res = analyzer.analyze_page("evidence001", 1, "q")
        assert res.error is not None and "vision_llm_failed" in res.error
        assert "人工看图" in res.note


class TestIngestHooks:
    def test_run_ingest_hooks(self, tmp_path):
        (tmp_path / ".openkb").mkdir()
        ent = tmp_path / "wiki" / "entities"
        ent.mkdir(parents=True)
        summ = tmp_path / "wiki" / "summaries"
        summ.mkdir(parents=True)
        (ent / "民法典第577条.md").write_text(
            '---\ntype: "Statute"\ndescription: "民法典第577条"\n---\n\n违约责任。\n',
            encoding="utf-8",
        )
        (summ / "case001.md").write_text(
            '---\ntype: "Summary"\n---\n\n适用[[entities/民法典第577条]]。\n', encoding="utf-8"
        )
        result = run_ingest_hooks(tmp_path, "case001")
        assert result.lifecycle_annotated
        assert result.graph_nodes >= 2
        assert result.errors == []

    def test_run_ingest_hooks_best_effort(self, tmp_path):
        # No wiki dir at all - hooks should not raise, just record errors.
        (tmp_path / ".openkb").mkdir()
        result = run_ingest_hooks(tmp_path, "nonexistent")
        assert result.doc_name == "nonexistent"
        # graph extract on empty wiki is fine (0 nodes); lifecycle annotate on missing page is False
        assert isinstance(result.errors, list)

    def test_run_maintenance_hooks(self, tmp_path):
        (tmp_path / ".openkb").mkdir()
        ent = tmp_path / "wiki" / "entities"
        ent.mkdir(parents=True)
        (ent / "x.md").write_text(
            '---\ntype: "Statute"\ndescription: "x"\n---\n\nbody\n', encoding="utf-8"
        )
        result = run_maintenance_hooks(tmp_path)
        assert result.graph_nodes >= 1
        assert isinstance(result.pages_needing_review, list)
