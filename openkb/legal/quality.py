"""Quality scoring + self-heal (spec section 3.12).

Every LLM-generated page gets a quality score (structural completeness, citation
sufficiency, consistency with existing knowledge). Below-threshold pages are
flagged for rewrite. Phase 3 ships a heuristic scorer (wikilinks = structure,
source URIs = citations, length = completeness); an LLM-based consistency check
layers on in a later pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from openkb.frontmatter import parse as parse_frontmatter
from openkb.frontmatter import split as split_frontmatter

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_SOURCE_URI_RE = re.compile(r"(docir://|law://|case://)[^\s)\]]+")
_MIN_BODY_LEN = 80  # chars; below this a page is skeletal
_REWRITE_THRESHOLD = 0.5

_PAGE_DIRS = ("concepts", "entities", "summaries")


@dataclass
class QualityScore:
    """Heuristic quality score for one wiki page."""

    page_path: str
    score: float
    needs_rewrite: bool
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "page_path": self.page_path,
            "score": round(self.score, 3),
            "needs_rewrite": self.needs_rewrite,
            "reasons": self.reasons,
        }


def score_page(kb_dir: Path | str, page_path: str) -> QualityScore:
    """Score a wiki page on structure + citation sufficiency (0.0-1.0)."""
    kb = Path(kb_dir).resolve()
    rel = page_path[:-3] if page_path.endswith(".md") else page_path
    p = (kb / "wiki" / rel).with_suffix(".md")
    reasons: List[str] = []
    if not p.exists():
        return QualityScore(
            page_path=page_path, score=0.0, needs_rewrite=True, reasons=["page missing"]
        )
    text = p.read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    body = parts[1] if parts else text
    fm = parse_frontmatter(text) if parts else {}

    score = 0.0
    # Structure: has wikilinks (cross-references).
    n_links = len(_WIKILINK_RE.findall(body))
    if n_links >= 1:
        score += 0.3
    else:
        reasons.append("no wikilinks (孤立页面)")
    if n_links >= 3:
        score += 0.1

    # Citation sufficiency: has typed source URIs.
    n_citations = len(_SOURCE_URI_RE.findall(body))
    if n_citations >= 1:
        score += 0.3
    else:
        reasons.append("no source citations (引用不充分)")
    if n_citations >= 2:
        score += 0.1

    # Completeness: body length.
    if len(body.strip()) >= _MIN_BODY_LEN:
        score += 0.2
    else:
        reasons.append(f"body too short (<{_MIN_BODY_LEN} chars)")

    # Frontmatter: has a description one-liner.
    if fm.get("description"):
        score += 0.1
    else:
        reasons.append("missing description")

    score = min(1.0, score)
    return QualityScore(
        page_path=page_path,
        score=score,
        needs_rewrite=score < _REWRITE_THRESHOLD,
        reasons=reasons,
    )


def score_all_pages(kb_dir: Path | str) -> List[QualityScore]:
    """Score every knowledge page. Returns scores sorted lowest-first."""
    kb = Path(kb_dir).resolve()
    scores: List[QualityScore] = []
    for subdir in _PAGE_DIRS:
        d = kb / "wiki" / subdir
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            scores.append(score_page(kb, f"{subdir}/{md.stem}"))
    scores.sort(key=lambda s: s.score)
    return scores


def pages_needing_rewrite(kb_dir: Path | str) -> List[QualityScore]:
    """Pages below the rewrite threshold (the self-heal work list)."""
    return [s for s in score_all_pages(kb_dir) if s.needs_rewrite]


__all__ = ["QualityScore", "score_page", "score_all_pages", "pages_needing_rewrite"]
