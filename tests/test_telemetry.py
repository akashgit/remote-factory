"""Tests for factory.telemetry — Langfuse tracing wrapper with mocked client."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import factory.telemetry as telemetry_mod


@pytest.fixture(autouse=True)
def _reset_telemetry():
    """Reset telemetry module state between tests."""
    old_client = telemetry_mod._client
    old_obs = telemetry_mod._observations.copy()
    telemetry_mod._client = None
    telemetry_mod._observations.clear()
    yield
    telemetry_mod._client = old_client
    telemetry_mod._observations.clear()
    telemetry_mod._observations.update(old_obs)


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
    def test_creates_trace_and_returns_tuple(self) -> None:
        mock_client = MagicMock()
        mock_obs = MagicMock()
        mock_obs.id = "span-abc"
        mock_obs.trace_id = "trace-abc"
        mock_client.start_observation.return_value = mock_obs
        telemetry_mod._client = mock_client

        with patch.object(telemetry_mod, "_set_trace_name_on_span"):
            result = telemetry_mod.begin_trace("my-project", "cycle-1", model="opus")

        assert result == ("trace-abc", "span-abc")
        mock_client.start_observation.assert_called_once_with(
            name="factory:my-project/cycle-1",
            as_type="span",
            input={"project": "my-project", "cycle_id": "cycle-1"},
            metadata={"model": "opus", "project": "my-project"},
        )

    def test_metadata_includes_none_model_when_omitted(self) -> None:
        mock_client = MagicMock()
        mock_obs = MagicMock()
        mock_obs.id = "span-xyz"
        mock_obs.trace_id = "trace-xyz"
        mock_client.start_observation.return_value = mock_obs
        telemetry_mod._client = mock_client

        with patch.object(telemetry_mod, "_set_trace_name_on_span"):
            telemetry_mod.begin_trace("proj", "c1")

        mock_client.start_observation.assert_called_once_with(
            name="factory:proj/c1",
            as_type="span",
            input={"project": "proj", "cycle_id": "c1"},
            metadata={"model": None, "project": "proj"},
        )


class TestBeginSpan:
    def test_creates_span_with_parent(self) -> None:
        mock_client = MagicMock()
        mock_parent = MagicMock()
        mock_child = MagicMock()
        mock_child.id = "span-123"
        mock_child.trace_id = "trace-1"
        mock_parent.start_observation.return_value = mock_child
        telemetry_mod._client = mock_client
        telemetry_mod._observations["parent-span"] = mock_parent

        result = telemetry_mod.begin_span("trace-1", "parent-span", "builder", model="sonnet")
        assert result == "span-123"
        mock_parent.start_observation.assert_called_once_with(
            name="agent:builder",
            as_type="span",
            input=None,
            metadata={"role": "builder", "model": "sonnet"},
        )

    def test_creates_span_without_parent(self) -> None:
        mock_client = MagicMock()
        mock_obs = MagicMock()
        mock_obs.id = "span-456"
        mock_obs.trace_id = "trace-1"
        mock_client.start_observation.return_value = mock_obs
        telemetry_mod._client = mock_client

        result = telemetry_mod.begin_span("trace-1", None, "researcher")
        assert result == "span-456"
        mock_client.start_observation.assert_called_once_with(
            trace_context={"trace_id": "trace-1"},
            name="agent:researcher",
            as_type="span",
            input=None,
            metadata={"role": "researcher", "model": None},
        )


class TestEndSpan:
    def test_records_usage_and_metadata(self) -> None:
        mock_client = MagicMock()
        mock_obs = MagicMock()
        telemetry_mod._client = mock_client
        telemetry_mod._observations["span-1"] = mock_obs

        telemetry_mod.end_span(
            "trace-1", "span-1",
            status="completed",
            usage={"input_tokens": 100, "output_tokens": 50, "total_cost_usd": 0.05},
            metadata={"extra": "data"},
            output="result text",
        )

        mock_obs.update.assert_called_once()
        call_kwargs = mock_obs.update.call_args[1]
        assert call_kwargs["output"] == "result text"
        assert call_kwargs["metadata"]["status"] == "completed"
        assert call_kwargs["metadata"]["input_tokens"] == 100
        assert call_kwargs["metadata"]["output_tokens"] == 50
        assert call_kwargs["metadata"]["total_cost_usd"] == 0.05
        assert call_kwargs["metadata"]["extra"] == "data"
        mock_obs.end.assert_called_once()
        assert "span-1" not in telemetry_mod._observations

    def test_handles_no_usage(self) -> None:
        mock_client = MagicMock()
        mock_obs = MagicMock()
        telemetry_mod._client = mock_client
        telemetry_mod._observations["span-1"] = mock_obs

        telemetry_mod.end_span("trace-1", "span-1", status="failed")

        call_kwargs = mock_obs.update.call_args[1]
        assert call_kwargs["metadata"]["status"] == "failed"
        mock_obs.end.assert_called_once()


class TestEndTrace:
    def test_marks_trace_completed(self) -> None:
        mock_client = MagicMock()
        mock_obs = MagicMock()
        telemetry_mod._client = mock_client
        telemetry_mod._observations["span-1"] = mock_obs

        telemetry_mod.end_trace("trace-1", span_id="span-1")

        mock_obs.update.assert_called_once_with(output={"status": "completed"})
        mock_obs.end.assert_called_once()
        assert "span-1" not in telemetry_mod._observations


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
        mock_parent = MagicMock()
        mock_tool_obs = MagicMock()
        mock_parent.start_observation.return_value = mock_tool_obs
        telemetry_mod._client = mock_client
        telemetry_mod._observations["span-1"] = mock_parent

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
            assert mock_parent.create_event.call_count >= 2
            assert mock_parent.start_observation.call_count >= 1
        finally:
            transcript_file.unlink(missing_ok=True)
            try:
                transcript_dir.rmdir()
            except OSError:
                pass
