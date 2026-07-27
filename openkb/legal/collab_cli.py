"""Click CLI for collaboration / sharing (``openkb collab ...``) - P4."""

from __future__ import annotations

from pathlib import Path

import click

from openkb.legal.sharing import (
    VISIBILITY_PRIVATE,
    VISIBILITY_SHARED,
    get_visibility,
    list_by_visibility,
    make_private,
    promote_to_shared,
)


def _resolve_kb(kb_dir: str | None) -> Path:
    return Path(kb_dir).resolve() if kb_dir else Path.cwd().resolve()


@click.group("collab")
def collab() -> None:
    """Page visibility / sharing (shared/private spaces)."""


@collab.command("show")
@click.argument("page")
@click.option("--kb-dir", "kb_dir", default=None)
def collab_show(page: str, kb_dir: str | None) -> None:
    """Show a page's visibility."""
    v = get_visibility(_resolve_kb(kb_dir), page)
    click.echo(f"{page}: {v}")


@collab.command("promote")
@click.argument("page")
@click.option("--kb-dir", "kb_dir", default=None)
def collab_promote(page: str, kb_dir: str | None) -> None:
    """Promote a personal note into the shared space."""
    promote_to_shared(_resolve_kb(kb_dir), page)
    click.echo(f"Promoted {page} -> {VISIBILITY_SHARED}")


@collab.command("private")
@click.argument("page")
@click.option("--kb-dir", "kb_dir", default=None)
def collab_private(page: str, kb_dir: str | None) -> None:
    """Mark a page private."""
    make_private(_resolve_kb(kb_dir), page)
    click.echo(f"Marked {page} -> {VISIBILITY_PRIVATE}")


@collab.command("list")
@click.option(
    "--visibility", "visibility", type=click.Choice(["private", "shared"]), default="shared"
)
@click.option("--kb-dir", "kb_dir", default=None)
def collab_list(visibility: str, kb_dir: str | None) -> None:
    """List pages by visibility."""
    pages = list_by_visibility(_resolve_kb(kb_dir), visibility)
    if not pages:
        click.echo(f"No {visibility} pages.")
        return
    for p in pages:
        click.echo(f"  {p['page_path']}")
    click.echo(f"\n{len(pages)} {visibility} page(s).")


__all__ = ["collab"]
