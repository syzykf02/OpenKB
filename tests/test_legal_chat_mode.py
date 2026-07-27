"""Tests for §4 legal chat mode wiring (build_chat_agent(legal=True))."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    (tmp_path / ".openkb").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "skills").mkdir()
    return tmp_path


def _tool_names(agent) -> set[str]:
    # agents.Agent.tools is a list of function-tool objects with a .name attr.
    return {getattr(t, "name", getattr(t, "__name__", str(t))) for t in agent.tools}


def test_default_chat_agent_has_no_legal_tools(kb):
    from openkb.agent.query import build_chat_agent

    agent = build_chat_agent(kb, "gpt-4o-mini", language="en")
    names = _tool_names(agent)
    assert not any(n.startswith("legal_") for n in names), names


def test_legal_chat_agent_adds_legal_tools_and_instructions(kb):
    from openkb.agent.query import build_chat_agent

    agent = build_chat_agent(kb, "gpt-4o-mini", language="en", legal=True)
    names = _tool_names(agent)
    for expected in (
        "legal_search",
        "legal_read_node",
        "legal_query_graph",
        "legal_find_impact",
        "legal_render_page",
        "legal_verify_citation",
    ):
        assert expected in names, f"{expected} missing from {names}"
    assert "Legal retrieval tools" in (agent.instructions or "")


def test_legal_chat_session_agent_threads_flag(kb):
    from openkb.agent.chat import build_chat_session_agent
    from openkb.agent.chat_session import ChatSession

    session = ChatSession.new(kb, model="gpt-4o-mini", language="en")
    agent = build_chat_session_agent(kb, session, legal=True)
    names = _tool_names(agent)
    assert "legal_verify_citation" in names
