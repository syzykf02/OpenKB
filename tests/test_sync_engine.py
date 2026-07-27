"""Tests for openkb.sync (apply_diff) + sync_cli (Phase 2.5)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from openkb.sync import SyncEngine, SyncSourceType


def _setup_kb(tmp_path: Path) -> Path:
    (tmp_path / ".openkb").mkdir()
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki" / "sources" / "images").mkdir(parents=True)
    return tmp_path


def _write(srcdir: Path, name: str, text: str = "# doc\n\nbody\n") -> Path:
    srcdir.mkdir(parents=True, exist_ok=True)
    p = srcdir / name
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def kb_with_source(tmp_path: Path):
    kb = _setup_kb(tmp_path)
    srcdir = kb / "cases"
    _write(srcdir, "case1.md", "# 案件1\n\n内容一。\n")
    _write(srcdir, "case2.md", "# 案件2\n\n内容二。\n")
    engine = SyncEngine(kb)
    engine.register_source("cases", SyncSourceType.LOCAL_DIR, path=str(srcdir), name="案件")
    return kb, srcdir, engine


class TestScanAndDiff:
    def test_scan_detects_new_files(self, kb_with_source):
        _kb, _src, engine = kb_with_source
        _source, _entries, diff = engine.scan_source("cases")
        assert len(diff.new_files) == 2
        assert "case1.md" in diff.new_files

    def test_scan_after_manifest_shows_unchanged(self, kb_with_source):
        _kb, _src, engine = kb_with_source
        source, entries, _diff = engine.scan_source("cases")
        engine.update_source_manifest("cases", entries)
        _source2, _entries2, diff2 = engine.scan_source("cases")
        assert len(diff2.new_files) == 0
        assert len(diff2.unchanged_files) == 2

    def test_scan_unknown_source_raises(self, kb_with_source):
        _kb, _src, engine = kb_with_source
        with pytest.raises(ValueError):
            engine.scan_source("nonexistent")


class TestApplyDiff:
    def test_apply_ingests_new_files(self, kb_with_source):
        kb, _src, engine = kb_with_source
        result = engine.apply_diff("cases")
        assert len(result.ingested) == 2
        assert all(outcome == "converted" for _, outcome in result.ingested)
        # .md + .docir.json produced
        assert (kb / "wiki" / "sources" / "case1.md").exists()
        assert (kb / "wiki" / "sources" / "case1.docir.json").exists()

    def test_apply_idempotent_second_run(self, kb_with_source):
        _kb, _src, engine = kb_with_source
        engine.apply_diff("cases")
        result2 = engine.apply_diff("cases")
        assert len(result2.ingested) == 0  # all unchanged now

    def test_apply_detects_modified(self, kb_with_source):
        _kb, src, engine = kb_with_source
        engine.apply_diff("cases")
        _write(src, "case1.md", "# 案件1 修改\n\n新内容。\n")
        result = engine.apply_diff("cases")
        assert len(result.ingested) == 1
        assert result.ingested[0][0] == "case1.md"

    def test_apply_records_deleted_not_removed(self, kb_with_source):
        kb, src, engine = kb_with_source
        engine.apply_diff("cases")  # ensure ingested
        engine.apply_diff("cases")  # establish baseline manifest
        (src / "case2.md").unlink()
        result = engine.apply_diff("cases")
        assert "case2.md" in result.deleted
        # KB artifacts are NOT auto-removed (deletion is human-gated)
        assert (kb / "wiki" / "sources" / "case2.md").exists()

    def test_apply_unknown_source(self, kb_with_source):
        _kb, _src, engine = kb_with_source
        result = engine.apply_diff("nonexistent")
        assert result.errors and "Unknown source" in result.errors[0]

    def test_apply_callback_used(self, kb_with_source):
        _kb, _src, engine = kb_with_source
        called = []

        def cb(src: Path) -> str:
            called.append(src.name)
            return "custom"

        result = engine.apply_diff("cases", ingest_callback=cb)
        assert all(outcome == "custom" for _, outcome in result.ingested)
        assert sorted(called) == ["case1.md", "case2.md"]

    def test_apply_result_total_changed(self, kb_with_source):
        _kb, _src, engine = kb_with_source
        result = engine.apply_diff("cases")
        assert result.total_changed == 2 or result.total_changed >= 2


class TestSyncCLI:
    def test_sync_group_registered(self):
        from openkb.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["sync", "--help"])
        assert res.exit_code == 0
        assert "add" in res.output and "apply" in res.output and "scan" in res.output

    def test_cli_add_scan_apply(self, tmp_path):
        from openkb.cli import cli

        kb = _setup_kb(tmp_path)
        srcdir = tmp_path / "cases"
        _write(srcdir, "case1.md", "# 案件1\n\n内容。\n")
        runner = CliRunner()
        assert (
            runner.invoke(
                cli, ["sync", "add", "cases", "--path", str(srcdir), "--kb-dir", str(kb)]
            ).exit_code
            == 0
        )
        scan = runner.invoke(cli, ["sync", "scan", "cases", "--kb-dir", str(kb)])
        assert "new:" in scan.output and "case1.md" in scan.output
        apply = runner.invoke(cli, ["sync", "apply", "cases", "--kb-dir", str(kb)])
        assert apply.exit_code == 0 and "converted" in apply.output
        assert (kb / "wiki" / "sources" / "case1.docir.json").exists()

    def test_cli_list(self, kb_with_source):
        from openkb.cli import cli

        kb, _src, _engine = kb_with_source
        runner = CliRunner()
        res = runner.invoke(cli, ["sync", "list", "--kb-dir", str(kb)])
        assert res.exit_code == 0 and "cases" in res.output
