"""Tests for openkb.legal.sharing + collab_cli (P4 collaboration)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from openkb.frontmatter import parse as parse_frontmatter
from openkb.legal.sharing import (
    VISIBILITY_PRIVATE,
    VISIBILITY_SHARED,
    get_visibility,
    list_by_visibility,
    make_private,
    promote_to_shared,
    set_visibility,
)


def _write_page(kb: Path, rel: str, fm: str = "", body: str = "body") -> None:
    p = kb / "wiki" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{fm}---\n\n{body}\n", encoding="utf-8")


@pytest.fixture
def kb_with_visibility(tmp_path: Path) -> Path:
    (tmp_path / ".openkb").mkdir()
    _write_page(tmp_path, "explorations/my-notes.md", fm='type: "Exploration"\n')
    _write_page(tmp_path, "concepts/违约责任.md", fm='type: "Concept"\n')
    return tmp_path


class TestSharing:
    def test_default_visibility_by_subdir(self, kb_with_visibility):
        # explorations default private, concepts default shared
        assert get_visibility(kb_with_visibility, "explorations/my-notes") == VISIBILITY_PRIVATE
        assert get_visibility(kb_with_visibility, "concepts/违约责任") == VISIBILITY_SHARED

    def test_set_and_read_visibility(self, kb_with_visibility):
        set_visibility(kb_with_visibility, "explorations/my-notes", VISIBILITY_SHARED)
        assert get_visibility(kb_with_visibility, "explorations/my-notes") == VISIBILITY_SHARED
        fm = parse_frontmatter(
            (kb_with_visibility / "wiki" / "explorations" / "my-notes.md").read_text(
                encoding="utf-8"
            )
        )
        assert fm["visibility"] == "shared"

    def test_promote_to_shared(self, kb_with_visibility):
        promote_to_shared(kb_with_visibility, "explorations/my-notes")
        assert get_visibility(kb_with_visibility, "explorations/my-notes") == VISIBILITY_SHARED

    def test_make_private(self, kb_with_visibility):
        make_private(kb_with_visibility, "concepts/违约责任")
        assert get_visibility(kb_with_visibility, "concepts/违约责任") == VISIBILITY_PRIVATE

    def test_invalid_visibility_raises(self, kb_with_visibility):
        with pytest.raises(ValueError):
            set_visibility(kb_with_visibility, "concepts/违约责任", "public")

    def test_missing_page_raises(self, kb_with_visibility):
        with pytest.raises(FileNotFoundError):
            set_visibility(kb_with_visibility, "concepts/nonexistent", VISIBILITY_SHARED)

    def test_list_by_visibility(self, kb_with_visibility):
        promote_to_shared(kb_with_visibility, "explorations/my-notes")
        shared = list_by_visibility(kb_with_visibility, VISIBILITY_SHARED)
        private = list_by_visibility(kb_with_visibility, VISIBILITY_PRIVATE)
        shared_paths = {p["page_path"] for p in shared}
        assert "explorations/my-notes" in shared_paths
        assert "concepts/违约责任" in shared_paths
        assert all(p["page_path"] != "explorations/my-notes" for p in private)

    def test_visibility_on_page_without_frontmatter(self, tmp_path):
        (tmp_path / ".openkb").mkdir()
        p = tmp_path / "wiki" / "explorations" / "bare.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("no frontmatter body\n", encoding="utf-8")
        set_visibility(tmp_path, "explorations/bare", VISIBILITY_SHARED)
        assert get_visibility(tmp_path, "explorations/bare") == VISIBILITY_SHARED


class TestCollabCLI:
    def test_collab_registered(self):
        from openkb.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["collab", "--help"])
        assert res.exit_code == 0
        assert "promote" in res.output and "private" in res.output

    def test_cli_promote_and_list(self, kb_with_visibility):
        from openkb.cli import cli

        runner = CliRunner()
        res = runner.invoke(
            cli,
            ["collab", "promote", "explorations/my-notes", "--kb-dir", str(kb_with_visibility)],
        )
        assert res.exit_code == 0 and "Promoted" in res.output
        res = runner.invoke(
            cli,
            ["collab", "list", "--visibility", "shared", "--kb-dir", str(kb_with_visibility)],
        )
        assert res.exit_code == 0 and "my-notes" in res.output

    def test_cli_show(self, kb_with_visibility):
        from openkb.cli import cli

        runner = CliRunner()
        res = runner.invoke(
            cli,
            ["collab", "show", "concepts/违约责任", "--kb-dir", str(kb_with_visibility)],
        )
        assert res.exit_code == 0 and "shared" in res.output
