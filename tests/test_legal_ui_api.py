"""Tests for the legal UI-supporting endpoints (§3.1 full graph + §5 docir-by-hash)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openkb.docir import KIND_DOCUMENT, KIND_SECTION, VISION_SIGNATURE, DocIRBuilder
from openkb.legal.graph_extract import extract_graph_from_wiki


@pytest.fixture
def kb(tmp_path: Path, monkeypatch) -> str:
    (tmp_path / ".openkb").mkdir()
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "wiki" / "summaries").mkdir(parents=True)
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "raw").mkdir(parents=True)
    (tmp_path / "wiki" / "entities" / "法条.md").write_text(
        '---\ntype: "Statute"\ndescription: "法条"\n---\n\n内容。\n', encoding="utf-8"
    )
    (tmp_path / "wiki" / "entities" / "案例.md").write_text(
        '---\ntype: "Case"\ndescription: "案例"\n---\n\n适用[[entities/法条]]。\n', encoding="utf-8"
    )
    extract_graph_from_wiki(tmp_path)
    # Build a DocIR doc + register its raw hash so by-hash resolves.
    b = DocIRBuilder("case001", input_type="pdf", converter="pageindex-tree", title="案例卷宗")
    r = b.add_node(kind=KIND_DOCUMENT, title="案例卷宗")
    b.add_node(kind=KIND_SECTION, title="证据", text="见下图", page=3, parent_id=r)
    b.add_visual_node(page=3, visual_type=VISION_SIGNATURE, text_anchor="签字处", render_ref="r.png", parent_id=r)
    b.build().save(tmp_path / "wiki" / "sources" / "case001.docir.json")
    raw = b"raw-pdf-bytes"
    (tmp_path / "raw" / "case001.pdf").write_bytes(raw)
    h = hashlib.sha256(raw).hexdigest()
    reg = {h: {"name": "case001.pdf", "doc_name": "case001", "path": "raw/case001.pdf"}}
    (tmp_path / ".openkb" / "hashes.json").write_text(json.dumps(reg))

    import openkb.api_helpers as api_h

    monkeypatch.setattr(api_h, "resolve_kb_alias", lambda name: tmp_path)
    monkeypatch.setattr("openkb.api_helpers.resolve_kb_alias", lambda name: tmp_path)
    return "testkb"


@pytest.fixture
def client():
    from openkb.api import create_app

    return TestClient(create_app())


class TestFullGraph:
    def test_full_graph_nodes_and_edges(self, kb, client):
        r = client.get("/api/v1/legal/graph", params={"kb": kb})
        assert r.status_code == 200
        data = r.json()
        assert len(data["nodes"]) >= 2
        assert len(data["edges"]) >= 1
        assert all("relation_type" in e for e in data["edges"])


class TestDocirByHash:
    def test_resolves_to_docir(self, kb, client):
        h = hashlib.sha256(b"raw-pdf-bytes").hexdigest()
        r = client.get(f"/api/v1/legal/docir/by-hash/{h}", params={"kb": kb})
        assert r.status_code == 200
        body = r.json()
        assert body["doc_name"] == "case001"
        assert body["docir"] is not None
        assert body["docir"]["root"] is not None
        # visual node present in the tree
        assert "vision" in json.dumps(body["docir"])

    def test_unknown_hash_returns_null(self, kb, client):
        r = client.get("/api/v1/legal/docir/by-hash/deadbeef", params={"kb": kb})
        assert r.status_code == 200
        body = r.json()
        assert body["docir"] is None
        assert body["doc_name"] is None

    def test_known_hash_missing_docir(self, kb, client, tmp_path: Path):
        # Register a hash whose doc_name has no .docir.json on disk.
        reg = json.loads((tmp_path / ".openkb" / "hashes.json").read_text())
        reg["aabb"] = {"name": "x.pdf", "doc_name": "nodocir", "path": "raw/x.pdf"}
        (tmp_path / ".openkb" / "hashes.json").write_text(json.dumps(reg))
        r = client.get("/api/v1/legal/docir/by-hash/aabb", params={"kb": kb})
        assert r.status_code == 200
        body = r.json()
        assert body["doc_name"] == "nodocir"
        assert body["docir"] is None
