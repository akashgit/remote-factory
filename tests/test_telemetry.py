"""Tests for factory.telemetry — Langfuse tracing wrapper with mocked client."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import factory.telemetry as telemetry_mod


@pytest.fixture(autouse=True)
def _reset_telemetry():
    """Reset telemetry module state between tests."""
    old_client = telemetry_mod._client
    telemetry_mod._client = None
    yield
    telemetry_mod._client = old_client


class TestIsEnabled:
    def test_returns_false_without_langfuse(self) -> None:
        with patch.object(telemetry_mod, "_HAS_LANGFUSE", False):
            assert telemetry_mod.is_enabled() is False

    def test_returns_false_without_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
        with patch.object(telemetry_mod, "_HAS_LANGFUSE", True):
            assert telemetry_mod.is_enabled() is False

    def test_returns_true_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        mock_client = MagicMock()
        mock_langfuse_cls = MagicMock(return_value=mock_client)
        monkeypatch.setattr(telemetry_mod, "_HAS_LANGFUSE", True)
        monkeypatch.setattr(telemetry_mod, "Langfuse", mock_langfuse_cls, raising=False)
        assert telemetry_mod.is_enabled() is True
        assert telemetry_mod._client is mock_client

    def test_returns_true_on_subsequent_calls(self) -> None:
        telemetry_mod._client = MagicMock()
        assert telemetry_mod.is_enabled() is True


class TestBeginTrace:
    def test_creates_trace_and_returns_id(self) -> None:
        mock_client = MagicMock()
        mock_trace = MagicMock()
        mock_trace.id = "trace-abc"
        mock_client.trace.return_value = mock_trace
        telemetry_mod._client = mock_client

        result = telemetry_mod.begin_trace("my-project", "cycle-1", model="opus")
        assert result == "trace-abc"
        mock_client.trace.assert_called_once_with(
            name="factory:my-project",
            session_id="cycle-1",
            metadata={"model": "opus"},
        )

    def test_no_metadata_when_no_model(self) -> None:
        mock_client = MagicMock()
        mock_trace = MagicMock()
        mock_trace.id = "trace-xyz"
        mock_client.trace.return_value = mock_trace
        telemetry_mod._client = mock_client

        telemetry_mod.begin_trace("proj", "c1")
        mock_client.trace.assert_called_once_with(
            name="factory:proj",
            session_id="c1",
            metadata=None,
        )


class TestBeginSpan:
    def test_creates_span_with_parent(self) -> None:
        mock_client = MagicMock()
        mock_span = MagicMock()
        mock_span.id = "span-123"
        mock_client.span.return_value = mock_span
        telemetry_mod._client = mock_client

        result = telemetry_mod.begin_span("trace-1", "parent-span", "builder", model="sonnet")
        assert result == "span-123"
        mock_client.span.assert_called_once_with(
            trace_id="trace-1",
            parent_observation_id="parent-span",
            name="agent:builder",
            metadata={"model": "sonnet"},
        )

    def test_creates_span_without_parent(self) -> None:
        mock_client = MagicMock()
        mock_span = MagicMock()
        mock_span.id = "span-456"
        mock_client.span.return_value = mock_span
        telemetry_mod._client = mock_client

        result = telemetry_mod.begin_span("trace-1", None, "researcher")
        assert result == "span-456"
        mock_client.span.assert_called_once_with(
            trace_id="trace-1",
            parent_observation_id=None,
            name="agent:researcher",
            metadata=None,
        )


class TestEndSpan:
    def test_records_usage_and_metadata(self) -> None:
        mock_client = MagicMock()
        telemetry_mod._client = mock_client

        telemetry_mod.end_span(
            "trace-1", "span-1",
            status="completed",
            usage={"input_tokens": 100, "output_tokens": 50, "total_cost_usd": 0.05},
            metadata={"extra": "data"},
            output="result text",
        )

        mock_client.span.assert_called_once()
        call_kwargs = mock_client.span.call_args[1]
        assert call_kwargs["id"] == "span-1"
        assert call_kwargs["trace_id"] == "trace-1"
        assert call_kwargs["output"] == "result text"
        assert call_kwargs["usage"] == {"input": 100, "output": 50}
        assert call_kwargs["metadata"]["status"] == "completed"
        assert call_kwargs["metadata"]["total_cost_usd"] == 0.05
        assert call_kwargs["metadata"]["extra"] == "data"

    def test_handles_no_usage(self) -> None:
        mock_client = MagicMock()
        telemetry_mod._client = mock_client

        telemetry_mod.end_span("trace-1", "span-1", status="failed")

        call_kwargs = mock_client.span.call_args[1]
        assert call_kwargs["usage"] is None
        assert call_kwargs["metadata"]["status"] == "failed"


class TestEndTrace:
    def test_marks_trace_completed(self) -> None:
        mock_client = MagicMock()
        telemetry_mod._client = mock_client

        telemetry_mod.end_trace("trace-1")
        mock_client.trace.assert_called_once_with(
            id="trace-1", metadata={"status": "completed"},
        )


class TestFlush:
    def test_flushes_when_client_exists(self) -> None:
        mock_client = MagicMock()
        telemetry_mod._client = mock_client
        telemetry_mod.flush()
        mock_client.flush.assert_called_once()

    def test_noop_when_no_client(self) -> None:
        telemetry_mod._client = None
        telemetry_mod.flush()


class TestIngestTranscript:
    def test_returns_false_when_no_transcript(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        telemetry_mod._client = mock_client

        result = telemetry_mod.ingest_transcript_to_span(
            "trace-1", "span-1", "nonexistent-session", tmp_path,
        )
        assert result is False

    def test_ingests_transcript_events(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_span_obj = MagicMock()
        mock_span_obj.id = "obs-tool-1"
        mock_client.span.return_value = mock_span_obj
        telemetry_mod._client = mock_client

        transcript = [
            {"type": "user", "message": {"content": [{"type": "text", "text": "Hello"}]}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Hi there"},
                {"type": "tool_use", "name": "Read", "input": {"path": "/foo"}, "id": "tu_1"},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": ["file contents"]},
            ]}},
        ]

        claude_dir = Path.home() / ".claude" / "projects"
        dir_name = str(tmp_path.resolve()).replace("/", "-").replace(".", "-")
        transcript_dir = claude_dir / dir_name
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_file = transcript_dir / "sess-123.jsonl"
        with open(transcript_file, "w") as f:
            for item in transcript:
                f.write(json.dumps(item) + "\n")

        try:
            result = telemetry_mod.ingest_transcript_to_span(
                "trace-1", "span-1", "sess-123", tmp_path,
            )
            assert result is True
            assert mock_client.event.call_count >= 2
            assert mock_client.span.call_count >= 1
        finally:
            transcript_file.unlink(missing_ok=True)
            try:
                transcript_dir.rmdir()
            except OSError:
                pass
