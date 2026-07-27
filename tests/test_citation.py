"""Tests for openkb.citation - the three-gate citation verification pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from openkb.citation import (
    RECENCY_NA,
    RECENCY_REPEALED,
    RECENCY_SUPERSEDED,
    CitationVerifier,
    format_verification,
    verify_citation,
)
from openkb.docir import KIND_DOCUMENT, KIND_SECTION
from openkb.legal.docir import (
    KIND_ARTICLE,
    LegalDocIRBuilder,
    STATUS_CURRENT,
    STATUS_REPEALED,
    STATUS_SUPERSEDED,
)


@pytest.fixture
def kb_with_citations(tmp_path: Path) -> Path:
    """KB with a current statute, a superseded rule, and a repealed rule."""
    sources = tmp_path / "wiki" / "sources"
    sources.mkdir(parents=True)

    # current statute: 民法典 第577条
    b = LegalDocIRBuilder("minfade", input_type="pdf", converter="pageindex-tree", title="民法典")
    r = b.add_node(kind=KIND_DOCUMENT, title="民法典")
    sec = b.add_node(kind=KIND_SECTION, title="合同编", page=47, parent_id=r)
    b.add_article(
        title="第577条",
        text="当事人一方不履行合同义务或者履行义务不符合约定的，应当承担违约责任。",
        statute="民法典", article_path="合同编/第577条", page=47, parent_id=sec,
    )
    b.set_effective_status(STATUS_CURRENT)
    b.build().save(sources / "minfade.docir.json")

    # superseded rule
    b2 = LegalDocIRBuilder("oldrule", input_type="pdf", converter="pageindex-tree", title="旧解释")
    r2 = b2.add_node(kind=KIND_DOCUMENT, title="旧解释")
    b2.add_article(
        title="第25条", text="民间借贷利率上限为年利率24%。",
        statute="旧解释", article_path="第25条", page=10, parent_id=r2,
    )
    b2.set_effective_status(STATUS_SUPERSEDED)
    b2.build().save(sources / "oldrule.docir.json")

    # repealed rule
    b3 = LegalDocIRBuilder("deadrule", input_type="pdf", converter="pageindex-tree", title="废止规定")
    r3 = b3.add_node(kind=KIND_DOCUMENT, title="废止规定")
    b3.add_article(
        title="第1条", text="某旧规定内容。",
        statute="废止规定", article_path="第1条", page=1, parent_id=r3,
    )
    b3.set_effective_status(STATUS_REPEALED)
    b3.build().save(sources / "deadrule.docir.json")

    return tmp_path


class TestExistenceGate:
    def test_existent_current_source_passes(self, kb_with_citations):
        res = verify_citation("违约责任", "law://民法典/合同编/第577条", kb_with_citations)
        assert res.exists
        assert res.doc_name == "minfade"

    def test_hallucinated_citation_fails_existence(self, kb_with_citations):
        res = verify_citation("claim", "law://不存在/第999条", kb_with_citations)
        assert not res.exists
        assert not res.passed
        assert any("existence" in m for m in res.messages)


class TestRecencyGate:
    def test_current_passes_recency(self, kb_with_citations):
        res = verify_citation("违约责任", "law://民法典/合同编/第577条", kb_with_citations)
        assert res.recency_ok
        assert res.effective_status == "current"

    def test_superseded_fails_recency(self, kb_with_citations):
        res = verify_citation("利率上限24%", "law://旧解释/第25条", kb_with_citations)
        assert res.exists
        assert not res.recency_ok
        assert res.effective_status == "superseded"
        assert any("SUPERSEDED" in m for m in res.messages)

    def test_repealed_fails_recency(self, kb_with_citations):
        res = verify_citation("旧规定", "law://废止规定/第1条", kb_with_citations)
        assert res.exists
        assert not res.recency_ok
        assert res.effective_status == "repealed"
        assert any("REPEALED" in m for m in res.messages)


class TestConsistencyGate:
    def test_consistent_claim_passes(self, kb_with_citations):
        res = verify_citation(
            "当事人一方不履行合同义务应当承担违约责任",
            "law://民法典/合同编/第577条", kb_with_citations,
        )
        assert res.consistency_ok
        assert res.consistency_score is not None and res.consistency_score > 0

    def test_unrelated_claim_fails_consistency(self, kb_with_citations):
        res = verify_citation(
            "完全无关的声明关于宇宙起源和量子力学",
            "law://民法典/合同编/第577条", kb_with_citations,
        )
        assert res.exists and res.recency_ok
        assert not res.consistency_ok
        assert any("consistency" in m for m in res.messages)

    def test_consistency_method_is_placeholder(self, kb_with_citations):
        res = verify_citation("违约责任", "law://民法典/合同编/第577条", kb_with_citations)
        assert "placeholder" in res.consistency_method


class TestOverallAndFormat:
    def test_all_gates_pass(self, kb_with_citations):
        res = verify_citation(
            "不履行合同义务承担违约责任",
            "law://民法典/合同编/第577条", kb_with_citations,
        )
        assert res.passed

    def test_format_verification_readable(self, kb_with_citations):
        res = verify_citation("违约责任", "law://民法典/合同编/第577条", kb_with_citations)
        text = format_verification(res)
        assert "Citation:" in text and "exists:" in text and "overall:" in text

    def test_non_legal_doc_recency_na(self, tmp_path):
        """A doc without extensions.legal gets recency n/a (ok)."""
        from openkb.docir import DocIRBuilder, KIND_PARAGRAPH

        sources = tmp_path / "wiki" / "sources"
        sources.mkdir(parents=True)
        b = DocIRBuilder("plain", input_type="md")
        root = b.add_node(kind=KIND_DOCUMENT, title="plain")
        b.add_node(kind=KIND_PARAGRAPH, text="some plain content here", parent_id=root)
        b.build().save(sources / "plain.docir.json")
        # resolve by the paragraph's docir:// id
        from openkb.docir import DocIRDocument

        doc = DocIRDocument.load(sources / "plain.docir.json")
        para = [n for n in doc.node_table().values() if n.kind == "paragraph"][0]
        res = verify_citation("plain content", para.id, tmp_path)
        assert res.exists
        assert res.effective_status == RECENCY_NA
        assert res.recency_ok  # non-legal docs pass recency


class TestCitationVerifierClass:
    def test_batch_verify(self, kb_with_citations):
        v = CitationVerifier(kb_with_citations)
        claims = [
            ("违约责任", "law://民法典/合同编/第577条"),
            ("claim", "law://不存在/第1条"),
            ("旧规定", "law://废止规定/第1条"),
        ]
        results = [v.verify(c, u) for c, u in claims]
        assert results[0].passed
        assert not results[1].exists
        assert not results[2].recency_ok
