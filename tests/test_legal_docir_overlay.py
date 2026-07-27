"""Tests for openkb.legal.docir - the legal DocIR overlay (spec section 8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from openkb.docir import KIND_SECTION
from openkb.legal.docir import (
    KIND_ARTICLE,
    KIND_EVIDENCE,
    KIND_HOLDING,
    LEGAL_EFFECTIVE_STATUSES,
    LEGAL_KINDS,
    LegalDocIRBuilder,
    STATUS_CURRENT,
    STATUS_REPEALED,
    STATUS_SUPERSEDED,
    case_uri,
    get_effective_status,
    is_legal_uri,
    law_uri,
    legal_anchor,
    legal_extension,
    set_effective_status,
)


class TestLegalKindsAndUris:
    def test_legal_kinds_present(self):
        assert KIND_ARTICLE in LEGAL_KINDS
        assert KIND_HOLDING in LEGAL_KINDS
        assert KIND_EVIDENCE in LEGAL_KINDS

    def test_law_uri(self):
        assert law_uri("民法典", "合同编", "第577条") == "law://民法典/合同编/第577条"
        assert law_uri("民法典") == "law://民法典"

    def test_case_uri(self):
        assert case_uri("2026民初1234", "卷3", "页47") == "case://2026民初1234/卷3/页47"

    def test_is_legal_uri(self):
        assert is_legal_uri("law://x")
        assert is_legal_uri("case://x")
        assert not is_legal_uri("docir://x")

    def test_legal_anchor(self):
        a = legal_anchor("docir://d/x", "law://s/a")
        assert a.default == "docir://d/x" and a.legal == "law://s/a"


class TestEffectiveStatus:
    def test_legal_extension_structure(self):
        ext = legal_extension(STATUS_CURRENT)
        assert ext == {"effective_status": "current"}

    def test_set_and_get_effective_status(self):
        b = LegalDocIRBuilder("m", input_type="pdf")
        b.set_effective_status(STATUS_SUPERSEDED)
        doc = b.build()
        assert get_effective_status(doc) == STATUS_SUPERSEDED

    def test_set_effective_status_validates(self):
        b = LegalDocIRBuilder("m", input_type="pdf")
        b.set_effective_status(STATUS_REPEALED)
        doc = b.build()
        with pytest.raises(ValueError):
            set_effective_status(doc, "not-a-status")

    def test_default_status_is_current(self):
        b = LegalDocIRBuilder("m", input_type="pdf")
        doc = b.build()
        assert get_effective_status(doc) == STATUS_CURRENT

    def test_legal_effective_statuses_complete(self):
        assert LEGAL_EFFECTIVE_STATUSES == {STATUS_CURRENT, STATUS_SUPERSEDED, STATUS_REPEALED}


class TestLegalDocIRBuilder:
    def test_add_article_with_law_anchor(self):
        b = LegalDocIRBuilder("minfade", input_type="pdf")
        root = b.add_node(kind=KIND_SECTION, title="合同编", page=47)
        art = b.add_article(
            title="第577条", text="违约责任", statute="民法典",
            article_path="合同编/第577条", page=47, parent_id=root,
        )
        doc = b.build()
        node = doc.get_node(art)
        assert node.kind == KIND_ARTICLE
        assert node.anchors.legal == "law://民法典/合同编/第577条"
        # resolves by law:// URI
        assert doc.resolve_uri("law://民法典/合同编/第577条") is not None

    def test_add_legal_node_with_case_anchor(self):
        b = LegalDocIRBuilder("case001", input_type="pdf")
        root = b.add_node(kind=KIND_SECTION, title="案卷")
        nid = b.add_legal_node(
            KIND_EVIDENCE, title="银行流水", text="", legal_uri_str="case://case001/卷3/页47",
            page=47, parent_id=root,
        )
        doc = b.build()
        assert doc.resolve_uri("case://case001/卷3/页47") is not None
        assert doc.get_node(nid).kind == KIND_EVIDENCE

    def test_overlay_round_trip(self, tmp_path: Path):
        b = LegalDocIRBuilder("minfade", input_type="pdf", converter="pageindex-tree")
        root = b.add_node(kind=KIND_SECTION, title="合同编", page=47)
        b.add_article(title="第577条", text="违约责任", statute="民法典", article_path="第577条", page=47, parent_id=root)
        b.set_effective_status(STATUS_SUPERSEDED)
        doc = b.build()
        p = tmp_path / "minfade.docir.json"
        doc.save(p)
        from openkb.docir import DocIRDocument

        loaded = DocIRDocument.load(p)
        assert get_effective_status(loaded) == STATUS_SUPERSEDED
        assert loaded.resolve_uri("law://民法典/第577条") is not None
        # core layer intact: docir:// still resolves
        art_node = [n for n in loaded.node_table().values() if n.kind == KIND_ARTICLE][0]
        assert loaded.resolve_uri(art_node.id) is not None


class TestCoreIndependence:
    """Strip extensions.legal + anchors.legal and DocIR is still complete."""

    def test_doc_without_legal_overlay_is_valid(self):
        from openkb.docir import DocIRBuilder, KIND_DOCUMENT, KIND_PARAGRAPH

        b = DocIRBuilder("plain", input_type="md")
        root = b.add_node(kind=KIND_DOCUMENT, title="plain")
        b.add_node(kind=KIND_PARAGRAPH, text="plain text", parent_id=root)
        doc = b.build()
        assert get_effective_status(doc) == STATUS_CURRENT  # default, no overlay
        assert doc.get_extension("legal") == {}  # empty overlay
