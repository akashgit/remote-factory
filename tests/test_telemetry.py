"""Tests for factory.telemetry — Langfuse tracing wrapper with mocked client."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import factory.telemetry as telemetry_mod

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "langfuse"))
from analyze_failure import find_matching_trace


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
        monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
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

    def test_returns_true_with_langfuse_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
        monkeypatch.setenv("LANGFUSE_BASE_URL", "https://langfuse.example.com")
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


class TestBeginTraceMetadata:
    def test_includes_benchmark_and_instance_id_from_env(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FACTORY_BENCHMARK", "swebench")
        monkeypatch.setenv("FACTORY_INSTANCE_ID", "django__django-12345")
        mock_client = MagicMock()
        mock_obs = MagicMock()
        mock_obs.id = "span-meta"
        mock_obs.trace_id = "trace-meta"
        mock_client.start_observation.return_value = mock_obs
        telemetry_mod._client = mock_client

        with patch.object(telemetry_mod, "_set_trace_name_on_span"):
            telemetry_mod.begin_trace("proj", "c1", model="opus")

        call_kwargs = mock_client.start_observation.call_args[1]
        assert call_kwargs["metadata"]["benchmark"] == "swebench"
        assert call_kwargs["metadata"]["instance_id"] == "django__django-12345"
        assert call_kwargs["metadata"]["model"] == "opus"
        assert call_kwargs["metadata"]["project"] == "proj"

    def test_omits_benchmark_keys_when_env_vars_absent(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FACTORY_BENCHMARK", raising=False)
        monkeypatch.delenv("FACTORY_INSTANCE_ID", raising=False)
        mock_client = MagicMock()
        mock_obs = MagicMock()
        mock_obs.id = "span-no-meta"
        mock_obs.trace_id = "trace-no-meta"
        mock_client.start_observation.return_value = mock_obs
        telemetry_mod._client = mock_client

        with patch.object(telemetry_mod, "_set_trace_name_on_span"):
            telemetry_mod.begin_trace("proj", "c1")

        call_kwargs = mock_client.start_observation.call_args[1]
        assert "benchmark" not in call_kwargs["metadata"]
        assert "instance_id" not in call_kwargs["metadata"]

    def test_includes_only_benchmark_when_instance_id_absent(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FACTORY_BENCHMARK", "featurebench")
        monkeypatch.delenv("FACTORY_INSTANCE_ID", raising=False)
        mock_client = MagicMock()
        mock_obs = MagicMock()
        mock_obs.id = "span-partial"
        mock_obs.trace_id = "trace-partial"
        mock_client.start_observation.return_value = mock_obs
        telemetry_mod._client = mock_client

        with patch.object(telemetry_mod, "_set_trace_name_on_span"):
            telemetry_mod.begin_trace("proj", "c1")

        call_kwargs = mock_client.start_observation.call_args[1]
        assert call_kwargs["metadata"]["benchmark"] == "featurebench"
        assert "instance_id" not in call_kwargs["metadata"]


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


class TestClaudeProjectsDir:
    def test_find_transcript_respects_claude_config_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        custom_dir = tmp_path / "custom-claude"
        project_path = tmp_path / "my-project"
        dir_name = str(project_path.resolve()).replace("/", "-").replace(".", "-")
        transcript_dir = custom_dir / "projects" / dir_name
        transcript_dir.mkdir(parents=True)
        transcript_file = transcript_dir / "sess-abc.jsonl"
        transcript_file.write_text('{"type":"user"}\n')

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(custom_dir))

        result = telemetry_mod._find_transcript("sess-abc", project_path)
        assert result is not None
        assert result == transcript_file

    def test_get_claude_projects_dir_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        result = telemetry_mod._get_claude_projects_dir()
        assert result == Path.home() / ".claude" / "projects"

    def test_get_claude_projects_dir_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/custom-claude")
        result = telemetry_mod._get_claude_projects_dir()
        assert result == Path("/tmp/custom-claude/projects")


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


class TestFindMatchingTrace:
    @staticmethod
    def _make_trace(
        trace_id: str,
        name: str = "",
        metadata: dict | None = None,
        start_time: str = "",
        latency: int = 0,
    ) -> dict:
        return {
            "id": trace_id,
            "name": name,
            "metadata": metadata or {},
            "startTime": start_time,
            "latency": latency,
        }

    def test_metadata_match_preferred_over_text_match(self) -> None:
        traces = [
            self._make_trace(
                "text-match", name="factory:swebench/cycle",
                start_time="2026-01-01T00:00:00Z", latency=100,
            ),
            self._make_trace(
                "meta-match", metadata={"benchmark": "swebench", "instance_id": "django-123"},
                start_time="2026-01-01T00:01:00Z", latency=10,
            ),
        ]
        with patch("analyze_failure.list_traces", return_value=traces):
            result = find_matching_trace(
                "swebench", "django-123",
                datetime(2026, 1, 1), 3600,
            )
        assert result is not None
        assert result["id"] == "meta-match"

    def test_no_fallback_to_all_traces_when_no_match(self) -> None:
        traces = [
            self._make_trace(
                "unrelated", name="factory:other/cycle",
                metadata={"benchmark": "other", "instance_id": "other-1"},
                start_time="2026-01-01T00:00:00Z", latency=500,
            ),
        ]
        with patch("analyze_failure.list_traces", return_value=traces):
            result = find_matching_trace(
                "swebench", "django-123",
                datetime(2026, 1, 1), 3600,
            )
        assert result is None

    def test_earliest_timestamp_wins_not_max_latency(self) -> None:
        traces = [
            self._make_trace(
                "late-high-latency",
                metadata={"benchmark": "swebench", "instance_id": "django-123"},
                start_time="2026-01-01T00:10:00Z", latency=9999,
            ),
            self._make_trace(
                "early-low-latency",
                metadata={"benchmark": "swebench", "instance_id": "django-123"},
                start_time="2026-01-01T00:01:00Z", latency=10,
            ),
        ]
        with patch("analyze_failure.list_traces", return_value=traces):
            result = find_matching_trace(
                "swebench", "django-123",
                datetime(2026, 1, 1), 3600,
            )
        assert result is not None
        assert result["id"] == "early-low-latency"

    def test_text_fallback_uses_earliest_timestamp(self) -> None:
        traces = [
            self._make_trace(
                "late", name="factory:swebench/cycle",
                start_time="2026-01-01T00:10:00Z", latency=500,
            ),
            self._make_trace(
                "early", name="factory:swebench/cycle",
                start_time="2026-01-01T00:01:00Z", latency=10,
            ),
        ]
        with patch("analyze_failure.list_traces", return_value=traces):
            result = find_matching_trace(
                "swebench", "other-id",
                datetime(2026, 1, 1), 3600,
            )
        assert result is not None
        assert result["id"] == "early"

    def test_returns_none_on_empty_traces(self) -> None:
        with patch("analyze_failure.list_traces", return_value=[]):
            result = find_matching_trace(
                "swebench", "django-123",
                datetime(2026, 1, 1), 3600,
            )
        assert result is None
