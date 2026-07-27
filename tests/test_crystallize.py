"""Tests for openkb.legal.crystallize + openkb.legal.quality (Phase 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from openkb.legal.crystallize import EvidenceItem, EvidencePack, crystallize
from openkb.legal.quality import pages_needing_rewrite, score_all_pages, score_page


@pytest.fixture
def kb_with_pages(tmp_path: Path) -> Path:
    (tmp_path / ".openkb").mkdir()
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "explorations").mkdir(parents=True)
    # a well-formed page: links + citations + description + length
    (tmp_path / "wiki" / "concepts" / "good.md").write_text(
        '---\ntype: "Concept"\ndescription: "违约责任原则"\n---\n\n'
        "参见[[entities/民法典第577条]]与[[entities/张某案]]。来源 law://民法典/第577条。\n"
        "本案适用该条文,构成违约。\n",
        encoding="utf-8",
    )
    # a skeletal page: no links, no citations, short, no description
    (tmp_path / "wiki" / "concepts" / "bad.md").write_text(
        '---\ntype: "Concept"\n---\n\n短。\n', encoding="utf-8"
    )
    return tmp_path


class TestCrystallize:
    def test_crystallize_writes_exploration_page(self, kb_with_pages):
        pack = EvidencePack(
            question="民间借贷利率上限?",
            answer="LPR四倍",
            confidence=0.92,
            evidence=[
                EvidenceItem(
                    claim="上限LPR四倍",
                    source="law://民间借贷解释/第25条",
                    source_type="司法解释",
                    status="current",
                ),
                EvidenceItem(
                    claim="流水87万",
                    source="case://2026民初1234/卷3/页47",
                    source_type="视觉证据",
                    vision_confidence=0.88,
                ),
            ],
            superseded_notices=["2020旧口径已取代"],
        )
        path = crystallize(pack, kb_with_pages)
        assert path.exists()
        assert path.parent.name == "explorations"
        text = path.read_text(encoding="utf-8")
        assert "LPR四倍" in text
        assert "law://民间借贷解释/第25条" in text
        assert "case://2026民初1234/卷3/页47" in text
        assert "Exploration" in text
        assert "2020旧口径已取代" in text

    def test_crystallize_custom_slug(self, kb_with_pages):
        pack = EvidencePack(question="q", answer="a", confidence=0.5)
        path = crystallize(pack, kb_with_pages, slug="custom-slug")
        assert path.name == "custom-slug.md"

    def test_evidence_item_markdown(self):
        item = EvidenceItem(claim="c", source="law://s/1", status="superseded")
        md = item.to_markdown()
        assert "c" in md and "law://s/1" in md and "superseded" in md


class TestQuality:
    def test_good_page_scores_high(self, kb_with_pages):
        s = score_page(kb_with_pages, "concepts/good")
        assert s.score >= 0.5
        assert not s.needs_rewrite

    def test_bad_page_flagged(self, kb_with_pages):
        s = score_page(kb_with_pages, "concepts/bad")
        assert s.score < 0.5
        assert s.needs_rewrite
        assert len(s.reasons) >= 3

    def test_missing_page(self, kb_with_pages):
        s = score_page(kb_with_pages, "concepts/nonexistent")
        assert s.score == 0.0 and s.needs_rewrite

    def test_score_all_sorted(self, kb_with_pages):
        scores = score_all_pages(kb_with_pages)
        assert len(scores) == 2
        assert scores[0].score <= scores[1].score  # lowest first

    def test_pages_needing_rewrite(self, kb_with_pages):
        nr = pages_needing_rewrite(kb_with_pages)
        assert any(s.page_path == "concepts/bad" for s in nr)
        assert all(not s.page_path.endswith("good") for s in nr)
