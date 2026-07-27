"""Lifecycle operations - sync knowledge lifecycle to wiki page frontmatter.

The lifecycle data structures live in :mod:`openkb.legal.lifecycle`
(``KnowledgePageLifecycle`` / ``LifecycleManager``). This module is the
**integration layer**: it reads/writes lifecycle fields on actual wiki pages'
YAML frontmatter (so humans and agents see confidence / status / superseded_by
inline) and drives the high-level operations:

- :func:`supersede_page` - mark a page superseded (status machine + history
  chain), the legal "version control" from spec section 3.6.
- :func:`confirm_page` - record a confirmation / add a source (boosts
  confidence, resets the decay clock).
- :func:`list_lifecycle_pages` - enumerate pages with their lifecycle state.

Frontmatter is the user-facing view; ``LifecycleManager`` (``.openkb/lifecycle/``)
is the durable structured store. Operations write both so they never drift.

Frontmatter fields use JSON-rendered scalars (``json.dumps`` is a strict YAML
subset), so ``confidence: 0.92`` stays a float and ``status: "superseded"``
stays a string - the existing :func:`openkb.frontmatter.set_line` quotes
everything as a string, which would break numeric fields.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from openkb.frontmatter import split as split_frontmatter
from openkb.legal.lifecycle import (
    DecayRate,
    KnowledgePageLifecycle,
    LifecycleManager,
)

# Wiki subdirs that carry knowledge pages with lifecycle frontmatter.
_LIFECYCLE_PAGE_DIRS = ("concepts", "entities", "summaries")

# Lifecycle frontmatter keys (the user-facing subset of KnowledgePageLifecycle).
_FM_KEYS = (
    "confidence",
    "sources_count",
    "last_confirmed",
    "status",
    "decay_rate",
    "version",
    "superseded_by",
    "superseded_at",
    "supersede_reason",
    "contradicted_by",
    "supersedes_list",
)


# ----------------------------------------------------------------------------
# Frontmatter field rendering (type-correct: json.dumps is valid YAML)
# ----------------------------------------------------------------------------
def _render_kv(key: str, value: Any) -> str:
    """Render ``key: <value>`` with json.dumps (valid YAML for scalars/lists)."""
    return f"{key}: {json.dumps(value, ensure_ascii=False)}"


def _drop_key(fm_block: str, key: str) -> str:
    """Remove any ``key:`` line from a frontmatter block."""
    import re

    return re.sub(rf"^{re.escape(key)}:.*\n?", "", fm_block, flags=re.MULTILINE)


def _set_field(fm_block: str, key: str, value: Any) -> str:
    """Set a frontmatter field (drop existing, insert rendered after opening ``---``)."""
    fm_block = _drop_key(fm_block, key)
    if value is None:
        return fm_block
    line = _render_kv(key, value) + "\n"
    return fm_block.replace("---\n", f"---\n{line}", 1)


def _page_path_to_abs(kb_dir: Path, page_path: str) -> Path:
    """Resolve a wiki-relative page path (``concepts/x`` or ``concepts/x.md``) to abs."""
    rel = page_path[:-3] if page_path.endswith(".md") else page_path
    return (kb_dir / "wiki" / rel).with_suffix(".md")


# ----------------------------------------------------------------------------
# Read / write a page's lifecycle from/to frontmatter
# ----------------------------------------------------------------------------
def read_page_lifecycle(kb_dir: Path, page_path: str) -> KnowledgePageLifecycle:
    """Read a wiki page's lifecycle from its frontmatter.

    Returns a fresh (default-active) lifecycle if the page has no lifecycle
    fields yet - so callers can always mutate then :func:`write_page_lifecycle`.
    """
    page_abs = _page_path_to_abs(kb_dir, page_path)
    if not page_abs.exists():
        return KnowledgePageLifecycle(page_path=page_path)
    text = page_abs.read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    if parts is None:
        return KnowledgePageLifecycle(page_path=page_path)
    fm = _parse_frontmatter(parts[0])
    return KnowledgePageLifecycle.from_frontmatter_dict(fm, page_path=page_path)


def _parse_frontmatter(fm_block: str) -> Dict[str, Any]:
    """Parse a frontmatter block to a dict (tolerant of lifecycle field types)."""
    import yaml

    inner = fm_block[3:]  # drop opening '---'
    close = inner.rfind("\n---")
    if close != -1:
        inner = inner[:close]
    try:
        data = yaml.safe_load(inner)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def write_page_lifecycle(kb_dir: Path, lifecycle: KnowledgePageLifecycle) -> None:
    """Merge lifecycle fields into a page's frontmatter (atomic write)."""
    from openkb.locks import atomic_write_text

    page_abs = _page_path_to_abs(kb_dir, lifecycle.page_path)
    if not page_abs.exists():
        return  # nothing to annotate
    text = page_abs.read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    if parts is None:
        # No frontmatter yet - create a minimal block.
        fm_block = "---\n---\n\n"
        body = text
    else:
        fm_block, body = parts
    fm_dict = lifecycle.to_frontmatter_dict()
    for key in _FM_KEYS:
        if key in fm_dict:
            fm_block = _set_field(fm_block, key, fm_dict[key])
        else:
            fm_block = _drop_key(fm_block, key)
    atomic_write_text(page_abs, fm_block + body)


# ----------------------------------------------------------------------------
# Operations
# ----------------------------------------------------------------------------
def supersede_page(
    kb_dir: Path,
    page_path: str,
    superseded_by: str,
    reason: str,
    triggered_by: str = "manual",
) -> KnowledgePageLifecycle:
    """Mark a knowledge page as superseded.

    Sets ``status: superseded`` + ``superseded_by`` / ``superseded_at`` /
    ``supersede_reason`` in the page's frontmatter, records the supersession in
    the durable ``LifecycleManager`` store, and adds the superseded page to the
    superseder's ``supersedes_list`` (if the superseder exists). The old page is
    NOT deleted - legal work needs historical rules (法不溯及既往).
    """
    mgr = LifecycleManager(kb_dir)
    lifecycle = mgr.get_lifecycle(page_path)
    lifecycle.supersede.mark_superseded(superseded_by, reason, triggered_by)
    lifecycle.bump_version()
    mgr.save_lifecycle(lifecycle)
    write_page_lifecycle(kb_dir, lifecycle)

    # Record the reverse link on the superseder, if it exists.
    if superseded_by:
        superseder = mgr.get_lifecycle(superseded_by)
        superseder.supersede.add_supersedes(page_path)
        mgr.save_lifecycle(superseder)
        try:
            write_page_lifecycle(kb_dir, superseder)
        except Exception:
            # The superseder page may not exist on disk yet - the durable store
            # still carries the link; don't fail the supersede over a write.
            pass
    return lifecycle


def confirm_page(
    kb_dir: Path,
    page_path: str,
    *,
    confidence: Optional[float] = None,
    add_source: bool = False,
    decay_rate: Optional[DecayRate] = None,
) -> KnowledgePageLifecycle:
    """Record a confirmation of a knowledge page (boosts confidence, resets decay)."""
    mgr = LifecycleManager(kb_dir)
    lifecycle = mgr.get_lifecycle(page_path)
    if decay_rate is not None:
        lifecycle.confidence.decay_rate = decay_rate
    if add_source:
        lifecycle.confidence.add_source()
    lifecycle.confidence.confirm(confirmation_confidence=confidence)
    lifecycle.bump_version()
    mgr.save_lifecycle(lifecycle)
    write_page_lifecycle(kb_dir, lifecycle)
    return lifecycle


def add_contradiction(
    kb_dir: Path, page_path: str, contradiction_id: str
) -> KnowledgePageLifecycle:
    """Declare a contradiction against a page (lowers confidence, records it)."""
    mgr = LifecycleManager(kb_dir)
    lifecycle = mgr.get_lifecycle(page_path)
    lifecycle.confidence.add_contradiction(contradiction_id)
    lifecycle.bump_version()
    mgr.save_lifecycle(lifecycle)
    write_page_lifecycle(kb_dir, lifecycle)
    return lifecycle


# ----------------------------------------------------------------------------
# Listing / queries
# ----------------------------------------------------------------------------
def list_lifecycle_pages(kb_dir: Path) -> List[Dict[str, Any]]:
    """Enumerate all knowledge pages with their lifecycle summary."""
    wiki = kb_dir / "wiki"
    results: List[Dict[str, Any]] = []
    for subdir in _LIFECYCLE_PAGE_DIRS:
        d = wiki / subdir
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            page_path = f"{subdir}/{md.stem}"
            lc = read_page_lifecycle(kb_dir, page_path)
            results.append(
                {
                    "page_path": page_path,
                    "status": lc.supersede.status.value if lc.supersede.status else "active",
                    "confidence": round(lc.confidence.confidence, 3),
                    "sources_count": lc.confidence.sources_count,
                    "decay_rate": lc.confidence.decay_rate.value,
                    "superseded_by": lc.supersede.superseded_by,
                    "last_confirmed": lc.confidence.last_confirmed.isoformat()
                    if lc.confidence.last_confirmed
                    else None,
                }
            )
    return results


def pages_needing_review(kb_dir: Path, confidence_threshold: float = 0.5) -> List[Dict[str, Any]]:
    """Pages that are active but low-confidence (candidates for re-confirmation)."""
    return [
        p
        for p in list_lifecycle_pages(kb_dir)
        if p["status"] == "active" and p["confidence"] < confidence_threshold
    ]


# ----------------------------------------------------------------------------
# Compile-time annotation (idempotent: add default lifecycle where missing)
# ----------------------------------------------------------------------------
def annotate_doc_lifecycle(
    kb_dir: Path,
    doc_name: str,
    *,
    decay_rate: DecayRate = DecayRate.SLOW,
    confidence: float = 0.7,
) -> bool:
    """Ensure a doc's summary page carries default lifecycle frontmatter.

    Idempotent: if the summary page already has lifecycle fields, it is left
    alone. Intended as a post-compile hook (Phase 3 wires it into the ingest
    event pipeline; for now it is callable via ``openkb lifecycle annotate``).
    Returns True if the page was annotated, False if it already had lifecycle
    or the page doesn't exist.
    """
    page_path = f"summaries/{doc_name}"
    page_abs = _page_path_to_abs(kb_dir, page_path)
    if not page_abs.exists():
        return False
    existing = read_page_lifecycle(kb_dir, page_path)
    # Already annotated if status is explicitly set beyond the default-active
    # baseline OR confidence was customized (default ConfidenceMetadata starts
    # at CONFIDENCE_MEDIUM with sources_count=0 - treat sources_count>0 or a
    # non-default confidence as "already annotated").
    if existing.confidence.sources_count > 0 or existing.version > 1:
        return False
    existing.confidence.confidence = confidence
    existing.confidence.decay_rate = decay_rate
    existing.confidence.sources_count = 1
    existing.confidence.confirm()
    existing.bump_version()
    mgr = LifecycleManager(kb_dir)
    mgr.save_lifecycle(existing)
    write_page_lifecycle(kb_dir, existing)
    return True


def annotate_all_pages(kb_dir: Path, **kwargs: Any) -> int:
    """Annotate every knowledge page missing lifecycle frontmatter. Returns count."""
    count = 0
    wiki = kb_dir / "wiki"
    for subdir in _LIFECYCLE_PAGE_DIRS:
        d = wiki / subdir
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            if annotate_doc_lifecycle(kb_dir, md.stem, **kwargs):
                count += 1
    return count


__all__ = [
    "read_page_lifecycle",
    "write_page_lifecycle",
    "supersede_page",
    "confirm_page",
    "add_contradiction",
    "list_lifecycle_pages",
    "pages_needing_review",
    "annotate_doc_lifecycle",
    "annotate_all_pages",
]
