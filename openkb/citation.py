"""Citation verification - the three-gate pipeline (domain-agnostic core).

Per ``spec/docir-format.md`` section 7, every Evidence Pack claim cites a
typed URI (``docir://`` generic, ``law://`` / ``case://`` legal). Verification
routes by scheme back to a DocIR node and runs three gates:

1. **Existence** - the cited node id resolves in the DocIR node table. A miss
   = hallucinated citation; reject outright. (Domain-agnostic.)
2. **Recency** - read the source's effective status. For legal docs this is
   ``extensions.legal.effective_status`` (``current`` / ``superseded`` /
   ``repealed``); a non-current source downgrades the answer and attaches the
   history chain. (Legal overlay; generic domains may swap in "version
   freshness".) The core exposes the gate; the legal layer supplies the field.
3. **Consistency** - re-fetch the source text via ``loc`` and check semantic
   consistency with the claim. Phase 0 ships a lexical-overlap placeholder;
   the LLM-based independent-prompt re-evaluation lands in Phase 2.

Gates 1 and 3 do not depend on the legal layer - any domain gets them. Gate 2
is the legal overlay's contribution (and degrades gracefully to "n/a" for
docs without ``extensions.legal``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openkb.agent.docir_tools import _load_all_docir
from openkb.docir import DocIRDocument, DocIRNode

# ----------------------------------------------------------------------------
# Verification result
# ----------------------------------------------------------------------------
# Recency outcomes
RECENCY_CURRENT = "current"
RECENCY_SUPERSEDED = "superseded"
RECENCY_REPEALED = "repealed"
RECENCY_NA = "n/a"  # no extensions.legal on the source doc (non-legal domain)

# Minimum lexical overlap (Jaccard) for the Phase-0 consistency placeholder.
_CONSISTENCY_THRESHOLD = 0.12


@dataclass
class VerificationResult:
    """Outcome of verifying one citation through the three gates."""

    source_uri: str
    exists: bool = False
    doc_name: Optional[str] = None
    node_id: Optional[str] = None
    source_text: Optional[str] = None
    # recency
    effective_status: str = RECENCY_NA
    recency_ok: bool = True
    # consistency (Phase-0 lexical placeholder; LLM re-eval is Phase 2)
    consistency_score: Optional[float] = None
    consistency_ok: bool = False
    consistency_method: str = "lexical-overlap-placeholder"
    # overall
    passed: bool = False
    messages: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_uri": self.source_uri,
            "exists": self.exists,
            "doc_name": self.doc_name,
            "node_id": self.node_id,
            "source_text": self.source_text,
            "effective_status": self.effective_status,
            "recency_ok": self.recency_ok,
            "consistency_score": self.consistency_score,
            "consistency_ok": self.consistency_ok,
            "consistency_method": self.consistency_method,
            "passed": self.passed,
            "messages": self.messages,
        }


# ----------------------------------------------------------------------------
# Verifier
# ----------------------------------------------------------------------------
class CitationVerifier:
    """Three-gate citation verifier over a KB's DocIR corpus.

    Construct with ``kb_root`` (the KB root, containing ``wiki/sources/``) and
    call :meth:`verify` per citation. Existence + recency + (lexical)
    consistency are all computed; the LLM-based consistency re-evaluation is
    Phase 2 - until then ``consistency_method`` flags the result as a
    placeholder so callers know not to treat a high score as a semantic check.
    """

    def __init__(self, kb_root: Path) -> None:
        self.kb_root = Path(kb_root).resolve()
        self.wiki_root = self.kb_root / "wiki"

    def verify(self, claim: str, source_uri: str) -> VerificationResult:
        """Run the three gates for one (claim, source_uri) pair."""
        result = VerificationResult(source_uri=source_uri)
        docs = _load_all_docir(self.wiki_root)

        # Gate 1: existence -------------------------------------------------
        node, doc = self._resolve(source_uri, docs)
        if node is None or doc is None:
            result.exists = False
            result.messages.append("existence: citation not found in DocIR (hallucinated?)")
            result.passed = False
            return result
        result.exists = True
        result.doc_name = doc.doc_name
        result.node_id = node.id
        result.source_text = node.text or node.title or ""

        # Gate 2: recency ---------------------------------------------------
        self._check_recency(result, doc)

        # Gate 3: consistency (Phase-0 lexical placeholder) -----------------
        self._check_consistency(result, claim)

        # Overall: existence + recency_ok + consistency_ok.
        result.passed = result.exists and result.recency_ok and result.consistency_ok
        return result

    # -- gate implementations ---------------------------------------------

    def _resolve(
        self, source_uri: str, docs: List[DocIRDocument]
    ) -> Tuple[Optional[DocIRNode], Optional[DocIRDocument]]:
        """Resolve a typed URI to (node, doc) across the DocIR corpus."""
        for doc in docs:
            node = doc.resolve_uri(source_uri)
            if node is not None:
                return node, doc
        return None, None

    def _check_recency(self, result: VerificationResult, doc: DocIRDocument) -> None:
        """Gate 2: read extensions.legal.effective_status (legal overlay)."""
        # The core reads the overlay defensively; it never assumes the legal
        # layer is present. Docs without extensions.legal get RECENCY_NA (ok).
        legal_ext = doc.get_extension("legal")
        if not legal_ext:
            result.effective_status = RECENCY_NA
            result.recency_ok = True
            return
        status = legal_ext.get("effective_status", RECENCY_CURRENT)
        result.effective_status = status
        if status == RECENCY_CURRENT:
            result.recency_ok = True
        elif status == RECENCY_SUPERSEDED:
            result.recency_ok = False
            superseded_by = legal_ext.get("superseded_by")
            chain = f" -> superseded_by: {superseded_by}" if superseded_by else ""
            result.messages.append(
                f"recency: source is SUPERSEDED (历史规则;法不溯及既往分析可能仍需){chain}"
            )
        elif status == RECENCY_REPEALED:
            result.recency_ok = False
            result.messages.append("recency: source is REPEALED (已废止)")
        else:
            # Unknown status - be conservative but don't hard-fail.
            result.recency_ok = True
            result.messages.append(f"recency: unknown effective_status {status!r}")

    def _check_consistency(self, result: VerificationResult, claim: str) -> None:
        """Gate 3: lexical-overlap placeholder for semantic consistency.

        Phase 0 only. A real LLM-based consistency re-evaluation (independent
        prompt) replaces this in Phase 2; until then the method field flags
        the result as a placeholder.
        """
        source = result.source_text or ""
        if not source:
            result.consistency_score = 0.0
            result.consistency_ok = False
            result.messages.append("consistency: source node has no text to check against")
            return
        score = _lexical_overlap(claim, source)
        result.consistency_score = round(score, 4)
        result.consistency_ok = score >= _CONSISTENCY_THRESHOLD
        if not result.consistency_ok:
            result.messages.append(
                f"consistency: low lexical overlap ({score:.2f}) - claim may not be "
                "supported by source (LLM re-eval pending)"
            )


# ----------------------------------------------------------------------------
# Lexical overlap (Phase-0 consistency placeholder)
# ----------------------------------------------------------------------------
def _lexical_overlap(a: str, b: str) -> float:
    """Jaccard overlap of CJK-char + ASCII-word token sets.

    A cheap stand-in for semantic consistency: if the claim and the source
    share few tokens, the claim is unlikely to be supported. The real gate
    (Phase 2) uses an independent LLM prompt.
    """
    from openkb.agent.docir_tools import _tokenize

    ta = set(_tokenize(a))
    tb = set(_tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ----------------------------------------------------------------------------
# Convenience
# ----------------------------------------------------------------------------
def verify_citation(claim: str, source_uri: str, kb_root: Path | str) -> VerificationResult:
    """Verify a single (claim, source_uri) citation. Returns a VerificationResult.

    Args:
        claim: The assertion to verify (e.g. ``"司法保护上限为LPR的四倍"``).
        source_uri: Typed URI (``docir://`` / ``law://`` / ``case://``).
        kb_root: Absolute path to the KB root.

    Returns:
        VerificationResult with existence / recency / consistency gate outcomes.
    """
    return CitationVerifier(Path(kb_root)).verify(claim, source_uri)


def format_verification(result: VerificationResult) -> str:
    """Render a VerificationResult as a readable string for the agent / UI."""
    lines: List[str] = []
    lines.append(f"Citation: {result.source_uri}")
    lines.append(f"  exists:       {'PASS' if result.exists else 'FAIL'}")
    if result.doc_name:
        lines.append(f"  doc:          {result.doc_name} ({result.node_id})")
    lines.append(
        f"  recency:      {'PASS' if result.recency_ok else 'FAIL'} ({result.effective_status})"
    )
    if result.consistency_score is not None:
        lines.append(
            f"  consistency:  {'PASS' if result.consistency_ok else 'FAIL'} "
            f"({result.consistency_score:.2f}, {result.consistency_method})"
        )
    lines.append(f"  overall:      {'PASS' if result.passed else 'FAIL'}")
    if result.messages:
        lines.append("  notes:")
        for msg in result.messages:
            lines.append(f"    - {msg}")
    return "\n".join(lines) + "\n"


__all__ = [
    "RECENCY_CURRENT",
    "RECENCY_SUPERSEDED",
    "RECENCY_REPEALED",
    "RECENCY_NA",
    "VerificationResult",
    "CitationVerifier",
    "verify_citation",
    "format_verification",
]
