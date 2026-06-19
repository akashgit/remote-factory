"""Tests for CEO cycle lifecycle — begin/complete traces and agent spans via telemetry."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.agents.runner import begin_cycle_session, complete_cycle_session
from factory.models import AgentRunResult, AgentUsage


@pytest.fixture
def _mock_telemetry(monkeypatch):
    """Patch telemetry module to return predictable IDs."""
    monkeypatch.delenv("FACTORY_TRACE_ID", raising=False)
    monkeypatch.delenv("FACTORY_PARENT_SPAN_ID", raising=False)
    with patch("factory.telemetry.is_enabled", return_value=True), \
         patch("factory.telemetry.begin_trace", return_value=("trace-001", "span-001")), \
         patch("factory.telemetry.begin_span", return_value="span-002"), \
         patch("factory.telemetry.end_span") as mock_end_span, \
         patch("factory.telemetry.end_trace") as mock_end_trace, \
         patch("factory.telemetry.flush") as mock_flush, \
         patch("factory.telemetry.ingest_transcript_to_span", return_value=True) as mock_ingest:
        yield {
            "end_span": mock_end_span,
            "end_trace": mock_end_trace,
            "flush": mock_flush,
            "ingest": mock_ingest,
        }


def test_begin_cycle_session_returns_span_id(tmp_path: Path, _mock_telemetry) -> None:
    span_id = begin_cycle_session(tmp_path, cycle_id="improve-2026-06-18")
    assert span_id == "span-001"


def test_begin_cycle_session_returns_none_when_disabled(tmp_path: Path) -> None:
    with patch("factory.telemetry.is_enabled", return_value=False):
        trace_id = begin_cycle_session(tmp_path, cycle_id="test")
    assert trace_id is None


def test_complete_cycle_session_calls_end_trace_and_flush(
    tmp_path: Path, _mock_telemetry, monkeypatch,
) -> None:
    monkeypatch.setenv("FACTORY_TRACE_ID", "trace-001")
    complete_cycle_session(tmp_path, "span-001")
    _mock_telemetry["end_trace"].assert_called_once_with("trace-001", span_id="span-001")
    _mock_telemetry["flush"].assert_called_once()


def test_complete_cycle_session_noop_when_none(tmp_path: Path, _mock_telemetry) -> None:
    complete_cycle_session(tmp_path, None)
    _mock_telemetry["end_trace"].assert_not_called()
    _mock_telemetry["flush"].assert_not_called()


async def test_invoke_agent_creates_span_and_threads_env(tmp_path: Path, _mock_telemetry) -> None:
    """invoke_agent creates a span and sets FACTORY_PARENT_SPAN_ID for child processes."""
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
        },
    )

    captured_env: dict[str, str | None] = {}

    async def mock_headless(request):
        captured_env["FACTORY_PARENT_SPAN_ID"] = os.environ.get("FACTORY_PARENT_SPAN_ID")
        captured_env["FACTORY_TRACE_ID"] = os.environ.get("FACTORY_TRACE_ID")
        return mock_result

    mock_runner = AsyncMock()
    mock_runner.headless = mock_headless

    old_trace = os.environ.get("FACTORY_TRACE_ID")
    old_span = os.environ.get("FACTORY_PARENT_SPAN_ID")
    os.environ["FACTORY_TRACE_ID"] = "trace-001"
    os.environ.pop("FACTORY_PARENT_SPAN_ID", None)
    try:
        with patch("factory.agents.runner.resolve_prompt", return_value="test prompt"), \
             patch("factory.agents.runner.get_runner", return_value=mock_runner):
            from factory.agents.runner import invoke_agent

            stdout, code = await invoke_agent(
                "builder",
                "Build something",
                tmp_path,
            )
    finally:
        if old_trace is not None:
            os.environ["FACTORY_TRACE_ID"] = old_trace
        else:
            os.environ.pop("FACTORY_TRACE_ID", None)
        if old_span is not None:
            os.environ["FACTORY_PARENT_SPAN_ID"] = old_span
        else:
            os.environ.pop("FACTORY_PARENT_SPAN_ID", None)

    assert code == 0
    assert stdout == "Task completed"
    assert captured_env["FACTORY_PARENT_SPAN_ID"] == "span-002"
    assert captured_env["FACTORY_TRACE_ID"] == "trace-001"

    _mock_telemetry["ingest"].assert_called_once_with(
        "trace-001", "span-002", "claude-abc123", tmp_path,
    )
    _mock_telemetry["end_span"].assert_called_once()


async def test_invoke_agent_restores_env_on_failure(tmp_path: Path, _mock_telemetry) -> None:
    """FACTORY_PARENT_SPAN_ID is restored even when the agent fails."""
    mock_runner = AsyncMock()
    mock_runner.headless = AsyncMock(side_effect=RuntimeError("boom"))

    old_trace = os.environ.get("FACTORY_TRACE_ID")
    old_span = os.environ.get("FACTORY_PARENT_SPAN_ID")
    os.environ["FACTORY_TRACE_ID"] = "trace-002"
    os.environ.pop("FACTORY_PARENT_SPAN_ID", None)
    try:
        with patch("factory.agents.runner.resolve_prompt", return_value="test"), \
             patch("factory.agents.runner.get_runner", return_value=mock_runner):
            from factory.agents.runner import invoke_agent

            stdout, code = await invoke_agent("builder", "fail", tmp_path, _track_failures=False)

        assert code == 1
        assert "FACTORY_PARENT_SPAN_ID" not in os.environ
    finally:
        if old_trace is not None:
            os.environ["FACTORY_TRACE_ID"] = old_trace
        else:
            os.environ.pop("FACTORY_TRACE_ID", None)
        if old_span is not None:
            os.environ["FACTORY_PARENT_SPAN_ID"] = old_span
        else:
            os.environ.pop("FACTORY_PARENT_SPAN_ID", None)
