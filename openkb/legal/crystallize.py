"""Crystallization - turn a finished analysis into a reusable wiki asset.

Per spec section 3.14, every completed case analysis / statute research /
class-case search is a reusable asset. Crystallization distills a finished
Evidence Pack into a structured ``wiki/explorations/`` page:

    问题是什么 -> 发现了什么 -> 涉及哪些条文/案例/实体 -> 沉淀了什么经验

The exploration page is a first-class wiki page (the spec's "Evidence Pack is
the entry point of knowledge compounding"). Writes go through ``atomic_write``
so the crash-safe invariant holds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from openkb.frontmatter import block, kv_line
from openkb.locks import atomic_write_text

_SAFE_SLUG_RE = re.compile(r"[^\w\-]+")


@dataclass
class EvidenceItem:
    """One piece of evidence supporting a claim in an Evidence Pack."""

    claim: str
    source: str  # typed URI (docir:// / law:// / case://)
    source_type: str = ""
    status: str = "current"  # current / superseded / repealed
    vision_confidence: Optional[float] = None

    def to_markdown(self) -> str:
        parts = [f"- **{self.claim}**", f"  - source: `{self.source}`"]
        if self.source_type:
            parts.append(f"  - type: {self.source_type}")
        if self.status != "current":
            parts.append(f"  - status: ⚠️ {self.status}")
        if self.vision_confidence is not None:
            parts.append(f"  - vision_confidence: {self.vision_confidence:.2f}")
        return "\n".join(parts)


@dataclass
class EvidencePack:
    """An Evidence Pack - the standard container for legal output (spec 3.10)."""

    question: str
    answer: str
    confidence: float = 0.0
    evidence: List[EvidenceItem] = field(default_factory=list)
    contradictions: List[str] = field(default_factory=list)
    superseded_notices: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "confidence": self.confidence,
            "evidence": [vars(e) for e in self.evidence],
            "contradictions": self.contradictions,
            "superseded_notices": self.superseded_notices,
        }


def _slugify(text: str, max_len: int = 40) -> str:
    """Slugify text for a wiki page stem."""
    slug = _SAFE_SLUG_RE.sub("-", text).strip("-") or "exploration"
    return slug[:max_len]


def crystallize(pack: EvidencePack, kb_dir: Path | str, *, slug: Optional[str] = None) -> Path:
    """Crystallize an Evidence Pack into a ``wiki/explorations/`` page.

    Returns the path of the written page. The page records the question, the
    answer with confidence, the evidence chain (each claim + its source URI +
    status), and any contradictions / superseded-notices - so the next similar
    query can reuse the analysis instead of re-deriving it.
    """
    kb = Path(kb_dir).resolve()
    stem = slug or _slugify(pack.question)
    out_path = kb / "wiki" / "explorations" / f"{stem}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fm_lines = [
        kv_line("type", "Exploration"),
        kv_line("description", pack.question[:120]),
        f"confidence: {pack.confidence:.2f}",
    ]
    frontmatter = block(fm_lines)

    parts: List[str] = [f"# {pack.question}\n"]
    parts.append(f"**Answer** (confidence {pack.confidence:.2f}):\n\n{pack.answer}\n")
    if pack.evidence:
        parts.append("## Evidence\n")
        parts.extend(e.to_markdown() for e in pack.evidence)
        parts.append("")
    if pack.contradictions:
        parts.append("## Contradictions\n")
        parts.extend(f"- {c}" for c in pack.contradictions)
        parts.append("")
    if pack.superseded_notices:
        parts.append("## Superseded notices\n")
        parts.extend(f"- {n}" for n in pack.superseded_notices)
        parts.append("")
    parts.append(
        "\n---\n*Crystallized from an Evidence Pack - reuse this analysis for "
        "similar queries rather than re-deriving it.*\n"
    )

    atomic_write_text(out_path, frontmatter + "\n".join(parts))
    return out_path


__all__ = ["EvidenceItem", "EvidencePack", "crystallize"]
