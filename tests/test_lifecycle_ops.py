"""Tests for openkb.legal.lifecycle_ops + lifecycle_cli (Phase 1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from openkb.frontmatter import parse as parse_frontmatter
from openkb.legal.lifecycle import DecayRate, DocumentStatus
from openkb.legal.lifecycle_ops import (
    add_contradiction,
    annotate_all_pages,
    annotate_doc_lifecycle,
    confirm_page,
    list_lifecycle_pages,
    pages_needing_review,
    read_page_lifecycle,
    supersede_page,
    write_page_lifecycle,
)


def _make_page(kb_dir: Path, page_path: str, body: str = "body", fm: str = "") -> Path:
    """Create a wiki page with frontmatter. page_path like 'concepts/x'."""
    rel = page_path[:-3] if page_path.endswith(".md") else page_path
    p = (kb_dir / "wiki" / rel).with_suffix(".md")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{fm}---\n\n{body}\n", encoding="utf-8")
    return p


@pytest.fixture
def kb_with_pages(tmp_path: Path) -> Path:
    (tmp_path / ".openkb").mkdir()
    _make_page(tmp_path, "concepts/违约责任", fm='type: "Concept"\ndescription: "x"\n')
    _make_page(tmp_path, "concepts/合同解除", fm='type: "Concept"\n')
    _make_page(tmp_path, "summaries/case001", fm='type: "Summary"\n')
    return tmp_path


class TestReadWriteFrontmatter:
    def test_read_default_lifecycle_when_absent(self, kb_with_pages):
        lc = read_page_lifecycle(kb_with_pages, "concepts/违约责任")
        assert lc.supersede.status == DocumentStatus.ACTIVE
        assert lc.page_path == "concepts/违约责任"

    def test_write_then_read_round_trip(self, kb_with_pages):
        lc = read_page_lifecycle(kb_with_pages, "concepts/违约责任")
        lc.confidence.confidence = 0.92
        lc.confidence.sources_count = 3
        lc.confidence.decay_rate = DecayRate.SLOW
        write_page_lifecycle(kb_with_pages, lc)
        # frontmatter types preserved
        fm = parse_frontmatter(
            (kb_with_pages / "wiki" / "concepts" / "违约责任.md").read_text(encoding="utf-8")
        )
        assert isinstance(fm["confidence"], float) and fm["confidence"] == 0.92
        assert isinstance(fm["sources_count"], int) and fm["sources_count"] == 3
        assert fm["status"] == "active"
        assert fm["decay_rate"] == "slow"
        # read back
        lc2 = read_page_lifecycle(kb_with_pages, "concepts/违约责任")
        assert lc2.confidence.confidence == 0.92

    def test_write_creates_frontmatter_when_absent(self, tmp_path):
        (tmp_path / ".openkb").mkdir()
        p = tmp_path / "wiki" / "concepts" / "x.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("no frontmatter body\n", encoding="utf-8")
        lc = read_page_lifecycle(tmp_path, "concepts/x")
        write_page_lifecycle(tmp_path, lc)
        text = p.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "status:" in text


class TestSupersede:
    def test_supersede_sets_frontmatter_and_store(self, kb_with_pages):
        lc = supersede_page(
            kb_with_pages,
            "concepts/合同解除",
            "concepts/合同解除_2024",
            reason="2024新司法解释",
            triggered_by="statute_change",
        )
        assert lc.supersede.is_superseded()
        assert lc.version >= 2
        fm = parse_frontmatter(
            (kb_with_pages / "wiki" / "concepts" / "合同解除.md").read_text(encoding="utf-8")
        )
        assert fm["status"] == "superseded"
        assert fm["superseded_by"] == "concepts/合同解除_2024"
        assert isinstance(fm["version"], int)
        # durable store carries it too
        from openkb.legal.lifecycle import LifecycleManager

        stored = LifecycleManager(kb_with_pages).get_lifecycle("concepts/合同解除")
        assert stored.supersede.is_superseded()

    def test_supersede_records_reverse_link(self, kb_with_pages):
        _make_page(kb_with_pages, "concepts/新规则", fm='type: "Concept"\n')
        supersede_page(
            kb_with_pages,
            "concepts/合同解除",
            "concepts/新规则",
            reason="r",
            triggered_by="manual",
        )
        lc_new = read_page_lifecycle(kb_with_pages, "concepts/新规则")
        assert "concepts/合同解除" in lc_new.supersede.supersedes_list


class TestConfirmAndContradiction:
    def test_confirm_adds_source_and_bumps_version(self, kb_with_pages):
        lc = confirm_page(kb_with_pages, "concepts/违约责任", confidence=0.95, add_source=True)
        assert lc.confidence.sources_count == 1
        assert lc.confidence.confidence > 0
        assert lc.version >= 2

    def test_confirm_sets_decay_rate(self, kb_with_pages):
        lc = confirm_page(kb_with_pages, "concepts/违约责任", decay_rate=DecayRate.FAST)
        assert lc.confidence.decay_rate == DecayRate.FAST

    def test_add_contradiction_lowers_confidence(self, kb_with_pages):
        before = read_page_lifecycle(kb_with_pages, "concepts/违约责任").confidence.confidence
        lc = add_contradiction(kb_with_pages, "concepts/违约责任", "contra-1")
        assert "contra-1" in lc.confidence.contradicted_by
        assert lc.confidence.confidence < before


class TestListAndReview:
    def test_list_includes_all_pages(self, kb_with_pages):
        pages = list_lifecycle_pages(kb_with_pages)
        paths = {p["page_path"] for p in pages}
        assert {"concepts/违约责任", "concepts/合同解除", "summaries/case001"} <= paths

    def test_list_reflects_supersede(self, kb_with_pages):
        supersede_page(kb_with_pages, "concepts/合同解除", "concepts/新", reason="r")
        pages = {p["page_path"]: p for p in list_lifecycle_pages(kb_with_pages)}
        assert pages["concepts/合同解除"]["status"] == "superseded"

    def test_pages_needing_review_filters(self, kb_with_pages):
        # lower confidence on one page
        lc = read_page_lifecycle(kb_with_pages, "concepts/违约责任")
        lc.confidence.confidence = 0.3
        write_page_lifecycle(kb_with_pages, lc)
        nr = pages_needing_review(kb_with_pages, confidence_threshold=0.5)
        assert any(p["page_path"] == "concepts/违约责任" for p in nr)
        assert all(p["confidence"] < 0.5 for p in nr)


class TestAnnotate:
    def test_annotate_adds_lifecycle(self, kb_with_pages):
        assert annotate_doc_lifecycle(kb_with_pages, "case001") is True
        fm = parse_frontmatter(
            (kb_with_pages / "wiki" / "summaries" / "case001.md").read_text(encoding="utf-8")
        )
        assert "confidence" in fm and fm["status"] == "active"

    def test_annotate_is_idempotent(self, kb_with_pages):
        annotate_doc_lifecycle(kb_with_pages, "case001")
        assert annotate_doc_lifecycle(kb_with_pages, "case001") is False

    def test_annotate_all_pages(self, kb_with_pages):
        count = annotate_all_pages(kb_with_pages)
        assert count >= 1

    def test_annotate_missing_doc(self, kb_with_pages):
        assert annotate_doc_lifecycle(kb_with_pages, "nonexistent") is False


class TestLifecycleCLI:
    def test_lifecycle_group_registered(self):
        from openkb.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["lifecycle", "--help"])
        assert res.exit_code == 0
        assert "supersede" in res.output and "confirm" in res.output

    def test_cli_supersede_and_list(self, kb_with_pages):
        from openkb.cli import cli

        runner = CliRunner()
        res = runner.invoke(
            cli,
            [
                "lifecycle",
                "supersede",
                "concepts/合同解除",
                "--by",
                "concepts/新",
                "--reason",
                "r",
                "--kb-dir",
                str(kb_with_pages),
            ],
        )
        assert res.exit_code == 0
        assert "Superseded" in res.output
        res = runner.invoke(cli, ["lifecycle", "list", "--kb-dir", str(kb_with_pages)])
        assert res.exit_code == 0
        assert "合同解除" in res.output and "superseded" in res.output

    def test_cli_show(self, kb_with_pages):
        from openkb.cli import cli

        supersede_page(kb_with_pages, "concepts/违约责任", "concepts/新", reason="r")
        runner = CliRunner()
        res = runner.invoke(
            cli,
            ["lifecycle", "show", "concepts/违约责任", "--kb-dir", str(kb_with_pages)],
        )
        assert res.exit_code == 0
        assert "Superseded by" in res.output

    def test_cli_annotate(self, kb_with_pages):
        from openkb.cli import cli

        runner = CliRunner()
        res = runner.invoke(
            cli, ["lifecycle", "annotate", "case001", "--kb-dir", str(kb_with_pages)]
        )
        assert res.exit_code == 0
        assert "Annotated" in res.output
