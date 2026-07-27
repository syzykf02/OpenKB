"""Tests for the legal/sync/visual REST endpoints (UI_INTEGRATION_PLAN §7)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openkb.legal.graph_extract import extract_graph_from_wiki


@pytest.fixture
def kb_with_legal_data(tmp_path: Path, monkeypatch) -> str:
    (tmp_path / ".openkb").mkdir()
    ent = tmp_path / "wiki" / "entities"
    ent.mkdir(parents=True)
    summ = tmp_path / "wiki" / "summaries"
    summ.mkdir(parents=True)
    (ent / "民法典第577条.md").write_text(
        '---\ntype: "Statute"\ndescription: "民法典第577条"\n---\n\n违约责任。\n', encoding="utf-8"
    )
    (ent / "张某案.md").write_text(
        '---\ntype: "Case"\ndescription: "张某案"\n---\n\n适用[[entities/民法典第577条]]。\n',
        encoding="utf-8",
    )
    (summ / "case001.md").write_text('---\ntype: "Summary"\n---\n\nbody\n', encoding="utf-8")
    extract_graph_from_wiki(tmp_path)
    # Patch resolve_kb_alias so "testkb" -> our temp KB (the API resolves KB by name).
    import openkb.api_helpers as h

    monkeypatch.setattr(h, "resolve_kb_alias", lambda name: tmp_path)
    monkeypatch.setattr("openkb.api_helpers.resolve_kb_alias", lambda name: tmp_path)
    return "testkb"


@pytest.fixture
def client():
    from openkb.api import create_app

    return TestClient(create_app())


class TestGraphEndpoints:
    def test_list_nodes(self, kb_with_legal_data, client):
        r = client.get("/api/v1/legal/graph/nodes", params={"kb": kb_with_legal_data})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 2
        assert any(n["node_type"] == "statute" for n in data["nodes"])

    def test_list_nodes_filter_by_type(self, kb_with_legal_data, client):
        r = client.get(
            "/api/v1/legal/graph/nodes", params={"kb": kb_with_legal_data, "node_type": "case"}
        )
        assert r.status_code == 200
        assert all(n["node_type"] == "case" for n in r.json()["nodes"])

    def test_impact(self, kb_with_legal_data, client):
        nodes = client.get("/api/v1/legal/graph/nodes", params={"kb": kb_with_legal_data}).json()[
            "nodes"
        ]
        statute = next(n for n in nodes if n["node_type"] == "statute")
        r = client.get(
            f"/api/v1/legal/graph/nodes/{statute['node_id']}/impact",
            params={"kb": kb_with_legal_data},
        )
        assert r.status_code == 200
        assert len(r.json()["affected"]) >= 1

    def test_contradictions(self, kb_with_legal_data, client):
        r = client.get("/api/v1/legal/graph/contradictions", params={"kb": kb_with_legal_data})
        assert r.status_code == 200
        assert "contradictions" in r.json()


class TestLifecycleEndpoints:
    def test_list_lifecycle(self, kb_with_legal_data, client):
        r = client.get("/api/v1/legal/lifecycle", params={"kb": kb_with_legal_data})
        assert r.status_code == 200
        assert r.json()["total"] >= 2

    def test_supersede(self, kb_with_legal_data, client):
        r = client.post(
            "/api/v1/legal/lifecycle/summaries/case001/supersede",
            params={"kb": kb_with_legal_data},
            json={
                "superseded_by": "summaries/case001_2024",
                "reason": "新解释",
                "triggered_by": "statute_change",
            },
        )
        assert r.status_code == 200
        assert r.json()["status"] == "superseded"

    def test_confirm(self, kb_with_legal_data, client):
        r = client.patch(
            "/api/v1/legal/lifecycle/summaries/case001/confidence",
            params={"kb": kb_with_legal_data},
            json={"add_source": True, "new_confidence": 0.9},
        )
        assert r.status_code == 200
        assert r.json()["sources_count"] >= 1


class TestSyncEndpoints:
    def test_list_sources_empty(self, kb_with_legal_data, client):
        r = client.get("/api/v1/legal/sync/sources", params={"kb": kb_with_legal_data})
        assert r.status_code == 200
        assert r.json()["source_count"] == 0

    def test_add_and_list_source(self, kb_with_legal_data, client, tmp_path: Path):
        srcdir = tmp_path / "cases"
        srcdir.mkdir()
        (srcdir / "a.md").write_text("# A\n", encoding="utf-8")
        r = client.post(
            "/api/v1/legal/sync/sources",
            params={"kb": kb_with_legal_data},
            json={"source_id": "cases", "path": str(srcdir)},
        )
        assert r.status_code == 200
        assert r.json()["source_id"] == "cases"
        r2 = client.get("/api/v1/legal/sync/sources", params={"kb": kb_with_legal_data})
        assert r2.json()["source_count"] == 1

    def test_scan_source(self, kb_with_legal_data, client, tmp_path: Path):
        srcdir = tmp_path / "cases"
        srcdir.mkdir()
        (srcdir / "a.md").write_text("# A\n", encoding="utf-8")
        client.post(
            "/api/v1/legal/sync/sources",
            params={"kb": kb_with_legal_data},
            json={"source_id": "cases", "path": str(srcdir)},
        )
        r = client.post("/api/v1/legal/sync/sources/cases/scan", params={"kb": kb_with_legal_data})
        assert r.status_code == 200
        assert r.json()["total_scanned"] >= 1


class TestVisualEndpoint:
    def test_visual_nodes_for_page_missing_doc(self, kb_with_legal_data, client):
        r = client.get("/api/v1/legal/visual/nonexistent/page/1", params={"kb": kb_with_legal_data})
        assert r.status_code == 200
        assert r.json()["error"] == "docir not found"
