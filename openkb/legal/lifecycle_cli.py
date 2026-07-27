"""Click CLI for knowledge lifecycle management (``openkb lifecycle ...``).

Command group registered into the main ``openkb`` CLI via
``cli.add_command(lifecycle)`` (a single-line registration in ``cli.py`` - the
command logic lives here so the grandfathered ``cli.py`` does not grow).

Commands:
    openkb lifecycle list                       list pages with lifecycle state
    openkb lifecycle show <page>                show one page's lifecycle
    openkb lifecycle supersede <page> --by <page> --reason <text>
    openkb lifecycle confirm <page> [--confidence N] [--add-source]
    openkb lifecycle annotate [<doc>]           add default lifecycle frontmatter
"""

from __future__ import annotations

from pathlib import Path

import click

from openkb.legal.lifecycle_ops import (
    annotate_all_pages,
    annotate_doc_lifecycle,
    confirm_page,
    list_lifecycle_pages,
    read_page_lifecycle,
    supersede_page,
)


def _resolve_kb(kb_dir: str | None) -> Path:
    """Resolve the KB root, defaulting to cwd (matching the rest of the CLI)."""
    return Path(kb_dir).resolve() if kb_dir else Path.cwd().resolve()


@click.group("lifecycle")
def lifecycle() -> None:
    """Knowledge lifecycle management (confidence / supersede / decay)."""


@lifecycle.command("list")
@click.option("--kb-dir", "kb_dir", default=None, help="Knowledge base root (default: cwd).")
@click.option(
    "--status", "status", default=None, help="Filter by status (active/superseded/repealed)."
)
def lifecycle_list(kb_dir: str | None, status: str | None) -> None:
    """List knowledge pages with their lifecycle state."""
    pages = list_lifecycle_pages(_resolve_kb(kb_dir))
    if status:
        pages = [p for p in pages if p["status"] == status]
    if not pages:
        click.echo("No pages found.")
        return
    click.echo(f"{'PAGE':40} {'STATUS':12} {'CONF':5} {'SRC':3} {'DECAY':6} SUPERSEDED_BY")
    for p in pages:
        click.echo(
            f"{p['page_path'][:40]:40} {p['status']:12} {p['confidence']:.2f}  "
            f"{p['sources_count']:<3} {p['decay_rate']:6} {p['superseded_by'] or ''}"
        )
    click.echo(f"\n{len(pages)} page(s).")


@lifecycle.command("show")
@click.argument("page")
@click.option("--kb-dir", "kb_dir", default=None)
def lifecycle_show(page: str, kb_dir: str | None) -> None:
    """Show one page's lifecycle (frontmatter view + durable store)."""
    lc = read_page_lifecycle(_resolve_kb(kb_dir), page)
    click.echo(f"Page:        {lc.page_path}")
    click.echo(f"Version:     {lc.version}")
    click.echo(f"Status:      {lc.supersede.status.value if lc.supersede.status else 'active'}")
    click.echo(f"Confidence:  {lc.confidence.confidence:.3f}")
    click.echo(f"Sources:     {lc.confidence.sources_count}")
    click.echo(f"Decay rate:  {lc.confidence.decay_rate.value}")
    if lc.confidence.last_confirmed:
        click.echo(f"Last confirmed: {lc.confidence.last_confirmed.isoformat()}")
    if lc.supersede.superseded_by:
        click.echo(f"Superseded by: {lc.supersede.superseded_by}")
        click.echo(f"Supersede reason: {lc.supersede.supersede_reason}")
    if lc.supersede.supersedes_list:
        click.echo(f"Supersedes:  {lc.supersede.supersedes_list}")
    if lc.confidence.contradicted_by:
        click.echo(f"Contradicted by: {lc.confidence.contradicted_by}")


@lifecycle.command("supersede")
@click.argument("page")
@click.option("--by", "superseded_by", required=True, help="Page that supersedes this one.")
@click.option("--reason", "reason", required=True, help="Supersession reason.")
@click.option(
    "--triggered-by",
    "triggered_by",
    default="manual",
    type=click.Choice(["manual", "sync", "contradiction", "statute_change", "import"]),
    help="What triggered the supersession.",
)
@click.option("--kb-dir", "kb_dir", default=None)
def lifecycle_supersede(
    page: str, superseded_by: str, reason: str, triggered_by: str, kb_dir: str | None
) -> None:
    """Mark a knowledge page as superseded (keeps history; does not delete)."""
    lc = supersede_page(_resolve_kb(kb_dir), page, superseded_by, reason, triggered_by)
    click.echo(
        f"Superseded {page} -> {superseded_by} (version {lc.version}, triggered by {triggered_by})."
    )


@lifecycle.command("confirm")
@click.argument("page")
@click.option(
    "--confidence", "confidence", type=float, default=None, help="Confirmation confidence."
)
@click.option("--add-source", is_flag=True, help="Increment the source count.")
@click.option(
    "--decay-rate",
    "decay_rate",
    type=click.Choice(["slow", "medium", "fast"]),
    default=None,
)
@click.option("--kb-dir", "kb_dir", default=None)
def lifecycle_confirm(
    page: str,
    confidence: float | None,
    add_source: bool,
    decay_rate: str | None,
    kb_dir: str | None,
) -> None:
    """Record a confirmation of a page (boosts confidence, resets decay)."""
    from openkb.legal.schema import DecayRate

    dr = DecayRate(decay_rate) if decay_rate else None
    lc = confirm_page(
        _resolve_kb(kb_dir), page, confidence=confidence, add_source=add_source, decay_rate=dr
    )
    click.echo(
        f"Confirmed {page}: confidence={lc.confidence.confidence:.3f}, "
        f"sources={lc.confidence.sources_count} (version {lc.version})."
    )


@lifecycle.command("annotate")
@click.argument("doc", required=False)
@click.option("--kb-dir", "kb_dir", default=None)
def lifecycle_annotate(doc: str | None, kb_dir: str | None) -> None:
    """Add default lifecycle frontmatter to pages missing it.

    With a DOC argument, annotates just that doc's summary page. Without,
    annotates every knowledge page missing lifecycle frontmatter.
    """
    root = _resolve_kb(kb_dir)
    if doc:
        done = annotate_doc_lifecycle(root, doc)
        click.echo(f"Annotated {doc}: {'yes' if done else 'already had lifecycle / not found'}.")
    else:
        count = annotate_all_pages(root)
        click.echo(f"Annotated {count} page(s).")


__all__ = ["lifecycle"]
