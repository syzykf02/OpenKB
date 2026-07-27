"""Click CLI for sync source management (``openkb sync ...``).

Command group registered into the main ``openkb`` CLI via
``cli.add_command(sync)`` (single-line registration in ``cli.py``); the command
logic lives here so the grandfathered ``cli.py`` does not grow.

Commands:
    openkb sync add <id> --path <dir> [--name N] [--auto]
    openkb sync list
    openkb sync scan <id>
    openkb sync apply <id>            ingest new/modified files (convert-only)
"""

from __future__ import annotations

from pathlib import Path

import click

from openkb.sync import SyncEngine, SyncSourceType


def _resolve_kb(kb_dir: str | None) -> Path:
    return Path(kb_dir).resolve() if kb_dir else Path.cwd().resolve()


@click.group("sync")
def sync() -> None:
    """Sync source management (folder import + continuous sync)."""


@sync.command("add")
@click.argument("source_id")
@click.option("--path", "path", required=True, help="Source directory path.")
@click.option("--name", "name", default=None, help="Display name.")
@click.option("--auto", "auto_sync", is_flag=True, help="Enable auto-sync.")
@click.option("--interval", "interval", type=int, default=60, help="Auto-sync interval (minutes).")
@click.option("--kb-dir", "kb_dir", default=None)
def sync_add(
    source_id: str,
    path: str,
    name: str | None,
    auto_sync: bool,
    interval: int,
    kb_dir: str | None,
) -> None:
    """Register a local-directory sync source."""
    engine = SyncEngine(_resolve_kb(kb_dir))
    source = engine.register_source(
        source_id,
        SyncSourceType.LOCAL_DIR,
        path=path,
        name=name,
        auto_sync=auto_sync,
        sync_interval_minutes=interval,
    )
    click.echo(
        f"Registered source '{source.source_id}' ({source.name}) -> {source.path} "
        f"[auto={auto_sync}]"
    )


@sync.command("list")
@click.option("--kb-dir", "kb_dir", default=None)
def sync_list(kb_dir: str | None) -> None:
    """List registered sync sources."""
    engine = SyncEngine(_resolve_kb(kb_dir))
    sources = engine.list_sources()
    if not sources:
        click.echo("No sync sources registered.")
        return
    click.echo(f"{'ID':20} {'TYPE':12} {'NAME':20} {'FILES':6} LAST_SYNC")
    stats = engine.stats()["sources"]
    for s in stats:
        click.echo(
            f"{s['source_id'][:20]:20} {s['type']:12} {(s['name'] or '')[:20]:20} "
            f"{s['file_count']:<6} {s['last_sync'] or 'never'}"
        )
    click.echo(f"\n{len(sources)} source(s).")


@sync.command("scan")
@click.argument("source_id")
@click.option("--kb-dir", "kb_dir", default=None)
def sync_scan(source_id: str, kb_dir: str | None) -> None:
    """Scan a source and show the diff vs the last manifest."""
    engine = SyncEngine(_resolve_kb(kb_dir))
    try:
        _source, _entries, diff = engine.scan_source(source_id)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1)
    click.echo(f"Diff for source '{source_id}':")
    click.echo(f"  new:      {len(diff.new_files)}")
    for f in diff.new_files[:20]:
        click.echo(f"    + {f}")
    click.echo(f"  modified: {len(diff.modified_files)}")
    for f in diff.modified_files[:20]:
        click.echo(f"    ~ {f}")
    click.echo(f"  deleted:  {len(diff.deleted_files)}")
    for f in diff.deleted_files[:20]:
        click.echo(f"    - {f}")
    click.echo(f"  unchanged: {len(diff.unchanged_files)}")


@sync.command("apply")
@click.argument("source_id")
@click.option(
    "--full",
    "full",
    is_flag=True,
    help="Run the full add pipeline (convert + compile). Default: convert-only (no LLM).",
)
@click.option("--kb-dir", "kb_dir", default=None)
def sync_apply(source_id: str, full: bool, kb_dir: str | None) -> None:
    """Apply a source's diff - ingest new/modified files.

    By default ingestion is convert-only (no LLM compile). With --full, the CLI
    wires the full ``openkb add`` pipeline (convert + compile + mutation).
    """
    engine = SyncEngine(_resolve_kb(kb_dir))
    callback = None
    if full:
        callback = _make_full_ingest_callback(_resolve_kb(kb_dir))
    try:
        result = engine.apply_diff(source_id, ingest_callback=callback)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1)
    if result.errors:
        for e in result.errors:
            click.echo(f"  [ERROR] {e}")
    for path, outcome in result.ingested:
        click.echo(f"  + {path}: {outcome}")
    for path in result.deleted:
        click.echo(f"  - {path} (recorded; not auto-removed)")
    click.echo(f"\nApplied {len(result.ingested)} ingest(s), {len(result.deleted)} deletion(s).")


def _make_full_ingest_callback(kb_dir: Path):
    """Build a callback that runs the full ingest pipeline (convert + compile).

    Convert-only is crash-safe (``convert_document`` holds the ingest lock);
    the compile step runs the LLM wiki compiler under the same lock. Long PDFs
    are converted only (their compile needs PageIndex, run via ``openkb add``).
    Lazy imports keep the sync module importable without the LLM stack.
    """

    def _ingest(src: Path) -> str:
        import asyncio

        from openkb.agent.compiler import compile_short_doc
        from openkb.config import resolve_effective_config
        from openkb.converter import convert_document
        from openkb.locks import kb_ingest_lock

        res = convert_document(src, kb_dir)
        if res.skipped:
            return "skipped"
        if res.is_long_doc or res.source_path is None:
            return "long_doc (convert only; compile via 'openkb add')"
        config = resolve_effective_config(kb_dir)[0]
        model = config.get("model", "gpt-4o-mini")
        with kb_ingest_lock(kb_dir / ".openkb"):
            asyncio.run(compile_short_doc(res.doc_name, res.source_path, kb_dir, model))
        return "converted+compiled"

    return _ingest


__all__ = ["sync"]
