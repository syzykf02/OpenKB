"""Collaboration - shared/private page visibility (spec section 3.13, P4).

Legal work mixes personal notes (case strategy, defense angles - private by
default) with team-reusable knowledge (promoted裁判规则 - shared). This module
is the page-level visibility layer:

- ``visibility: private|shared`` frontmatter field (default private for
  ``explorations/``, shared for ``concepts/``).
- :func:`promote_to_shared` - the "推广" action: a personal note promoted into
  the shared space for team reuse.
- :func:`list_by_visibility` - scope filter for the shared/private views.

Writes go through ``atomic_write`` (the crash-safe invariant). P4 is marked
optional in the spec's roadmap; this is its base implementation - multi-agent
merge sync and fine-grained permissions layer on later.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from openkb.frontmatter import parse as parse_frontmatter
from openkb.frontmatter import split as split_frontmatter
from openkb.locks import atomic_write_text

VISIBILITY_PRIVATE = "private"
VISIBILITY_SHARED = "shared"
VALID_VISIBILITIES = {VISIBILITY_PRIVATE, VISIBILITY_SHARED}

# Default visibility by page subdir (spec: personal notes private, promoted
# rules shared). Summaries/entities default shared (compiled knowledge).
_DEFAULT_VISIBILITY = {
    "explorations": VISIBILITY_PRIVATE,
    "concepts": VISIBILITY_SHARED,
    "entities": VISIBILITY_SHARED,
    "summaries": VISIBILITY_SHARED,
}


def _page_abs(kb_dir: Path, page_path: str) -> Path:
    rel = page_path[:-3] if page_path.endswith(".md") else page_path
    return (kb_dir / "wiki" / rel).with_suffix(".md")


def _subdir_of(page_path: str) -> str:
    rel = page_path[:-3] if page_path.endswith(".md") else page_path
    return rel.split("/", 1)[0] if "/" in rel else ""


def get_visibility(kb_dir: Path | str, page_path: str) -> str:
    """Read a page's visibility (default per subdir if unset)."""
    kb = Path(kb_dir).resolve()
    p = _page_abs(kb, page_path)
    if not p.exists():
        return _DEFAULT_VISIBILITY.get(_subdir_of(page_path), VISIBILITY_PRIVATE)
    text = p.read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    if parts is None:
        return _DEFAULT_VISIBILITY.get(_subdir_of(page_path), VISIBILITY_PRIVATE)
    fm = parse_frontmatter(parts[0])
    v = str(fm.get("visibility", "")).strip().lower()
    return (
        v
        if v in VALID_VISIBILITIES
        else _DEFAULT_VISIBILITY.get(_subdir_of(page_path), VISIBILITY_PRIVATE)
    )


def set_visibility(kb_dir: Path | str, page_path: str, visibility: str) -> None:
    """Set a page's visibility (``private`` / ``shared``). Atomic write."""
    if visibility not in VALID_VISIBILITIES:
        raise ValueError(
            f"visibility must be one of {sorted(VALID_VISIBILITIES)}, got {visibility!r}"
        )
    kb = Path(kb_dir).resolve()
    p = _page_abs(kb, page_path)
    if not p.exists():
        raise FileNotFoundError(page_path)
    text = p.read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    if parts is None:
        fm_block, body = f"---\nvisibility: {visibility}\n---\n\n", text
    else:
        fm_block, body = parts
        fm_block = _set_field(fm_block, "visibility", visibility)
    atomic_write_text(p, fm_block + body)


def promote_to_shared(kb_dir: Path | str, page_path: str) -> str:
    """Promote a personal note into the shared space (the 推广 action).

    Sets ``visibility: shared``. Returns the new visibility.
    """
    set_visibility(kb_dir, page_path, VISIBILITY_SHARED)
    return VISIBILITY_SHARED


def make_private(kb_dir: Path | str, page_path: str) -> str:
    """Mark a page private (personal notes / defense strategy)."""
    set_visibility(kb_dir, page_path, VISIBILITY_PRIVATE)
    return VISIBILITY_PRIVATE


def list_by_visibility(kb_dir: Path | str, visibility: str) -> List[Dict[str, Any]]:
    """List pages with a given visibility, across knowledge + exploration dirs."""
    if visibility not in VALID_VISIBILITIES:
        raise ValueError(f"visibility must be one of {sorted(VALID_VISIBILITIES)}")
    kb = Path(kb_dir).resolve()
    results: List[Dict[str, Any]] = []
    for subdir in ("concepts", "entities", "summaries", "explorations"):
        d = kb / "wiki" / subdir
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            page_path = f"{subdir}/{md.stem}"
            if get_visibility(kb, page_path) == visibility:
                results.append({"page_path": page_path, "visibility": visibility})
    return results


def _set_field(fm_block: str, key: str, value: str) -> str:
    """Set a scalar frontmatter field (drop existing, insert after opening ---)."""
    fm_block = re.sub(rf"^{re.escape(key)}:.*\n?", "", fm_block, flags=re.MULTILINE)
    line = f"{key}: {value}\n"
    return fm_block.replace("---\n", f"---\n{line}", 1)


__all__ = [
    "VISIBILITY_PRIVATE",
    "VISIBILITY_SHARED",
    "VALID_VISIBILITIES",
    "get_visibility",
    "set_visibility",
    "promote_to_shared",
    "make_private",
    "list_by_visibility",
]
