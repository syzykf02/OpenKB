"""Legal DocIR overlay - domain config on top of the domain-agnostic DocIR core.

Per ``spec/docir-format.md`` section 8, the legal layer adds three things to
the canonical DocIR core - all additive, none polluting the core:

- **kind aliases** - ``article`` / ``judgment_main`` / ``evidence`` / ``holding``
  / ``finding`` / ``citation`` / ``preamble``. The core passes ``kind`` through
  as an opaque string, so these are just documented constants.
- **anchors.legal** - ``law://`` and ``case://`` URI schemes. Citation
  verification routes by scheme: ``law://`` / ``case://`` resolve against
  ``anchors.legal`` (``docir://`` resolves against ``anchors.default``).
- **extensions.legal** - currently just ``effective_status``
  (``current`` / ``superseded`` / ``repealed``), the basis of the citation
  verification **recency gate**. ``superseded_by`` / ``revision_history`` and
  other lifecycle fields are added in Phase 1 (lifecycle integration).

Strip ``extensions.legal`` and ``anchors.legal`` and DocIR is still a complete
generic document representation - the hard constraint from the spec.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from openkb.docir import (
    KIND_DOCUMENT,
    KIND_PARAGRAPH,
    KIND_SECTION,
    SCHEME_CASE,
    SCHEME_LAW,
    DocIRAnchors,
    DocIRBuilder,
    DocIRDocument,
    DocIRFormat,
    DocIRLoc,
    DocIRNode,
    DocIRProvenance,
    DocIRSource,
    DocIRVision,
)
from openkb.docir import (
    create_docir_from_markdown as _create_docir_from_markdown,
)

# ----------------------------------------------------------------------------
# Legal kind aliases (core passes kind through as an opaque string)
# ----------------------------------------------------------------------------
KIND_ARTICLE = "article"  # 法条 / 条文
KIND_JUDGMENT_MAIN = "judgment_main"  # 判决主文
KIND_EVIDENCE = "evidence"  # 证据
KIND_HOLDING = "holding"  # 裁判规则 / 本院认为
KIND_FINDING = "finding"  # 法院认定事实
KIND_CITATION = "citation"  # 援引
KIND_PREAMBLE = "preamble"  # 首部（当事人/案由/法院）

LEGAL_KINDS = {
    KIND_ARTICLE,
    KIND_JUDGMENT_MAIN,
    KIND_EVIDENCE,
    KIND_HOLDING,
    KIND_FINDING,
    KIND_CITATION,
    KIND_PREAMBLE,
}

# ----------------------------------------------------------------------------
# effective_status values (extensions.legal.effective_status)
# ----------------------------------------------------------------------------
STATUS_CURRENT = "current"  # 现行有效
STATUS_SUPERSEDED = "superseded"  # 已被取代
STATUS_REPEALED = "repealed"  # 已废止

LEGAL_EFFECTIVE_STATUSES = {STATUS_CURRENT, STATUS_SUPERSEDED, STATUS_REPEALED}


# ----------------------------------------------------------------------------
# Legal anchor URI builders
# ----------------------------------------------------------------------------
def law_uri(statute: str, *path_parts: str) -> str:
    """Build a ``law://<statute>/<path>`` legal anchor URI.

    Example: ``law_uri("民法典", "合同编", "第577条")`` ->
    ``"law://民法典/合同编/第577条"``.
    """
    parts = "/".join(p for p in (statute, *path_parts) if p)
    return f"law://{parts}"


def case_uri(case_id: str, *path_parts: str) -> str:
    """Build a ``case://<case_id>/<path>`` legal anchor URI.

    Example: ``case_uri("2026民初1234", "卷3", "页47")`` ->
    ``"case://2026民初1234/卷3/页47"``.
    """
    parts = "/".join(p for p in (case_id, *path_parts) if p)
    return f"case://{parts}"


def legal_anchor(default: str, legal: Optional[str] = None) -> DocIRAnchors:
    """Build typed anchors carrying an optional legal URI (law:// or case://)."""
    return DocIRAnchors(default=default, legal=legal)


def is_legal_uri(uri: str) -> bool:
    """True for ``law://`` / ``case://`` URIs (vs generic ``docir://``)."""
    return uri.startswith(f"{SCHEME_LAW}://") or uri.startswith(f"{SCHEME_CASE}://")


# ----------------------------------------------------------------------------
# extensions.legal helpers
# ----------------------------------------------------------------------------
def legal_extension(effective_status: str = STATUS_CURRENT, **extra: Any) -> Dict[str, Any]:
    """Build the value to store under ``extensions.legal``.

    Returns the inner dict (``{"effective_status": ...}``) - store it via
    ``doc.set_extension("legal", legal_extension(...))``. Currently only
    ``effective_status`` is defined; Phase 1 (lifecycle) adds
    ``superseded_by`` / ``superseded_at`` / ``revision_history`` here.
    """
    data: Dict[str, Any] = {"effective_status": effective_status}
    data.update(extra)
    return data


def get_effective_status(doc: DocIRDocument) -> str:
    """Read ``extensions.legal.effective_status`` (default ``current``)."""
    return doc.get_extension("legal").get("effective_status", STATUS_CURRENT)


def set_effective_status(doc: DocIRDocument, status: str) -> None:
    """Write ``extensions.legal.effective_status`` (recency-gate basis)."""
    if status not in LEGAL_EFFECTIVE_STATUSES:
        raise ValueError(
            f"effective_status must be one of {sorted(LEGAL_EFFECTIVE_STATUSES)}, got {status!r}"
        )
    ext = doc.get_extension("legal")
    ext["effective_status"] = status
    doc.set_extension("legal", ext)


# ----------------------------------------------------------------------------
# Legal DocIR builder - legal-overlay conveniences on the canonical builder
# ----------------------------------------------------------------------------
class LegalDocIRBuilder(DocIRBuilder):
    """DocIR builder with legal-overlay conveniences.

    Adds legal kind aliases (``article`` / ``judgment_main`` / ``evidence``),
    legal anchors (``law://`` / ``case://``), and the ``extensions.legal``
    effective_status field on top of the domain-agnostic
    :class:`openkb.docir.DocIRBuilder`.
    """

    def add_article(
        self,
        title: str,
        text: str = "",
        *,
        statute: Optional[str] = None,
        article_path: Optional[str] = None,
        page: Optional[int] = None,
        parent_id: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Add an article node (``kind=article``) with a ``law://`` legal anchor.

        If ``statute`` is given, builds ``law://<statute>/<article_path>`` as
        the legal anchor so citation verification can resolve it by ``law://``.
        """
        legal = law_uri(statute, article_path) if statute else None
        nid = self._next_id(KIND_ARTICLE, title)
        return self.add_node(
            kind=KIND_ARTICLE,
            text=text,
            title=title,
            page=page,
            parent_id=parent_id,
            node_id=nid,
            anchors=legal_anchor(nid, legal),
            **kwargs,
        )

    def add_legal_node(
        self,
        kind: str,
        title: str,
        *,
        text: str = "",
        legal_uri_str: Optional[str] = None,
        page: Optional[int] = None,
        parent_id: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Add a node with an explicit legal kind + optional legal anchor URI."""
        nid = self._next_id(kind, title)
        return self.add_node(
            kind=kind,
            text=text,
            title=title,
            page=page,
            parent_id=parent_id,
            node_id=nid,
            anchors=legal_anchor(nid, legal_uri_str),
            **kwargs,
        )

    def set_effective_status(self, status: str) -> "LegalDocIRBuilder":
        """Set ``extensions.legal.effective_status`` on the built document."""
        self.set_extension("legal", legal_extension(status))
        return self


# ----------------------------------------------------------------------------
# Re-exports (canonical types, so ``openkb.legal`` is a superset of the core)
# ----------------------------------------------------------------------------
__all__ = [
    # legal kinds
    "KIND_ARTICLE",
    "KIND_JUDGMENT_MAIN",
    "KIND_EVIDENCE",
    "KIND_HOLDING",
    "KIND_FINDING",
    "KIND_CITATION",
    "KIND_PREAMBLE",
    "LEGAL_KINDS",
    # effective status
    "STATUS_CURRENT",
    "STATUS_SUPERSEDED",
    "STATUS_REPEALED",
    "LEGAL_EFFECTIVE_STATUSES",
    # URI helpers
    "law_uri",
    "case_uri",
    "legal_anchor",
    "is_legal_uri",
    # extensions.legal helpers
    "legal_extension",
    "get_effective_status",
    "set_effective_status",
    # builder
    "LegalDocIRBuilder",
    # canonical re-exports
    "DocIRNode",
    "DocIRDocument",
    "DocIRBuilder",
    "DocIRAnchors",
    "DocIRLoc",
    "DocIRProvenance",
    "DocIRVision",
    "DocIRSource",
    "DocIRFormat",
    "KIND_DOCUMENT",
    "KIND_SECTION",
    "KIND_PARAGRAPH",
    "create_docir_from_markdown",
]


def create_docir_from_markdown(
    markdown: str,
    doc_name: str,
    *,
    effective_status: str = STATUS_CURRENT,
    legal: bool = False,
    **kwargs: Any,
) -> DocIRDocument:
    """Build a shallow DocIR from Markdown, optionally with a legal overlay.

    When ``legal=True`` (or ``effective_status`` is set), attaches
    ``extensions.legal = {effective_status}`` so the recency gate has something
    to read.
    """
    doc = _create_docir_from_markdown(markdown, doc_name, **kwargs)
    if legal or effective_status != STATUS_CURRENT:
        set_effective_status(doc, effective_status)
    return doc
