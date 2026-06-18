"""Tests for CEO session lifecycle — begin/complete cycle sessions with child linking."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.agents.runner import begin_cycle_session, complete_cycle_session
from factory.models import AgentRunResult, AgentUsage
from factory.sessions import get_children, get_session, get_sessions


def test_begin_cycle_session_creates_root(tmp_path: Path) -> None:
    sid = begin_cycle_session(tmp_path, cycle_id="improve-2026-06-18")

    session = get_session(tmp_path, sid)
    assert session is not None
    assert session["agent_role"] == "ceo"
    assert session["kind"] == "default"
    assert session["title"] == "improve-2026-06-18"
    assert session["status"] == "running"
    assert session["root_id"] == sid


def test_begin_cycle_session_without_cycle_id(tmp_path: Path) -> None:
    sid = begin_cycle_session(tmp_path)

    session = get_session(tmp_path, sid)
    assert session is not None
    assert session["title"] is None
    assert session["agent_role"] == "ceo"


def test_begin_cycle_session_with_model(tmp_path: Path) -> None:
    sid = begin_cycle_session(tmp_path, model="claude-opus-4-6")

    session = get_session(tmp_path, sid)
    assert session is not None
    assert session["model"] == "claude-opus-4-6"


def test_complete_cycle_session_aggregates_children(tmp_path: Path) -> None:
    from factory.sessions import begin_session, complete_session

    root_id = begin_cycle_session(tmp_path, cycle_id="test-cycle")

    child1 = begin_session(tmp_path, "researcher", parent_id=root_id, root_id=root_id)
    usage1 = AgentUsage(
        input_tokens=100, output_tokens=50, total_cost_usd=0.05, duration_ms=1000.0,
    )
    complete_session(tmp_path, child1, usage=usage1)

    child2 = begin_session(tmp_path, "builder", parent_id=root_id, root_id=root_id)
    usage2 = AgentUsage(
        input_tokens=200, output_tokens=100, total_cost_usd=0.10, duration_ms=2000.0,
    )
    complete_session(tmp_path, child2, usage=usage2)

    complete_cycle_session(tmp_path, root_id)

    session = get_session(tmp_path, root_id)
    assert session is not None
    assert session["status"] == "completed"
    assert session["total_cost_usd"] == pytest.approx(0.15)
    assert session["input_tokens"] == 300
    assert session["output_tokens"] == 150
    assert session["duration_ms"] == pytest.approx(3000.0)


def test_complete_cycle_session_no_children(tmp_path: Path) -> None:
    root_id = begin_cycle_session(tmp_path)

    complete_cycle_session(tmp_path, root_id)

    session = get_session(tmp_path, root_id)
    assert session is not None
    assert session["status"] == "completed"
    assert session["total_cost_usd"] == 0.0
    assert session["input_tokens"] == 0


async def test_invoke_agent_links_to_parent(tmp_path: Path) -> None:
    """invoke_agent with parent_session_id creates a child session linked to the root."""
    root_id = begin_cycle_session(tmp_path, cycle_id="e2e-test")

    mock_result = AgentRunResult(
        stdout="Task completed",
        return_code=0,
        usage=AgentUsage(
            input_tokens=500, output_tokens=200,
            total_cost_usd=0.08, duration_ms=5000.0,
            num_turns=4, model="claude-sonnet-4-6",
        ),
        metadata={
            "session_id": "claude-abc123",
            "stop_reason": "end_turn",
            "terminal_reason": "end_turn",
        },
    )

    mock_runner = AsyncMock()
    mock_runner.headless = AsyncMock(return_value=mock_result)

    with patch("factory.agents.runner.resolve_prompt", return_value="test prompt"), \
         patch("factory.agents.runner.get_runner", return_value=mock_runner):
        from factory.agents.runner import invoke_agent

        (_PROMPTS_DIR := tmp_path / ".factory" / "agents").mkdir(parents=True, exist_ok=True)

        stdout, code = await invoke_agent(
            "builder",
            "Build something",
            tmp_path,
            parent_session_id=root_id,
        )

    assert code == 0
    assert stdout == "Task completed"

    children = get_children(tmp_path, root_id)
    assert len(children) == 1
    assert children[0]["agent_role"] == "builder"
    assert children[0]["parent_id"] == root_id

    child_session = get_session(tmp_path, children[0]["id"])
    assert child_session is not None
    assert child_session["status"] == "completed"
    assert child_session["input_tokens"] == 500
    assert child_session["output_tokens"] == 200
    assert child_session["total_cost_usd"] == pytest.approx(0.08)
    assert child_session["claude_session_id"] == "claude-abc123"

    complete_cycle_session(tmp_path, root_id)
    root = get_session(tmp_path, root_id)
    assert root is not None
    assert root["status"] == "completed"
    assert root["total_cost_usd"] == pytest.approx(0.08)
    assert root["input_tokens"] == 500


def test_sessions_filter_by_cycle(tmp_path: Path) -> None:
    """get_sessions with cycle_id returns only sessions in that cycle."""
    from factory.sessions import begin_session

    root1 = begin_cycle_session(tmp_path, cycle_id="cycle-1")
    begin_session(tmp_path, "builder", parent_id=root1, root_id=root1)

    root2 = begin_cycle_session(tmp_path, cycle_id="cycle-2")
    begin_session(tmp_path, "researcher", parent_id=root2, root_id=root2)

    cycle1_sessions = get_sessions(tmp_path, cycle_id=root1)
    assert len(cycle1_sessions) == 2
    roles = {s["agent_role"] for s in cycle1_sessions}
    assert roles == {"ceo", "builder"}

    cycle2_sessions = get_sessions(tmp_path, cycle_id=root2)
    assert len(cycle2_sessions) == 2
    roles = {s["agent_role"] for s in cycle2_sessions}
    assert roles == {"ceo", "researcher"}


def test_standalone_session_backward_compat(tmp_path: Path) -> None:
    """Sessions created without parent_session_id are standalone (backward compat)."""
    from factory.sessions import begin_session

    sid = begin_session(tmp_path, "builder")
    session = get_session(tmp_path, sid)
    assert session is not None
    assert session["kind"] == "default"
    assert session["parent_id"] is None
    assert session["root_id"] == sid
