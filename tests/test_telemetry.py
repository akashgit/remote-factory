"""Tests for factory.telemetry — Langfuse tracing wrapper with mocked client."""

from __future__ import annotations

import json
import sys
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import factory.telemetry as telemetry_mod

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "langfuse"))
from analyze_failure import _find_trial_log, find_matching_trace, generate_report, main


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


class TestGenerateReportFallback:
    @staticmethod
    def _result_data(
        solver: str = "factory",
        exception: str = "",
        benchmark: str = "swebench",
        instance_id: str = "django-123",
        timestamp: str = "20260101T000000Z",
    ) -> dict:
        data: dict = {
            "benchmark": benchmark,
            "instance_id": instance_id,
            "solver": solver,
            "duration_seconds": 120,
            "resolved": False,
            "timestamp": timestamp,
        }
        if exception:
            data["details"] = {"exception": exception}
        return data

    def test_claude_code_solver_not_short_circuited(self, tmp_path: Path) -> None:
        data = self._result_data(solver="claude-code", exception="RuntimeError: timeout after 300s")
        result_json = tmp_path / "result.json"
        result_json.write_text(json.dumps(data))

        with patch("analyze_failure.run_llm_analysis"), patch("analyze_failure.run_llm_summary"):
            with patch("sys.argv", ["analyze_failure", str(result_json), "--no-llm"]):
                with patch("analyze_failure._write_output") as mock_write:
                    main()
        report = mock_write.call_args[0][0]
        assert "RuntimeError: timeout after 300s" in report

    def test_trial_log_fallback_no_trace(self, tmp_path: Path) -> None:
        data = self._result_data(timestamp="20260101T000000Z", benchmark="swebench")
        trial_log = tmp_path / "20260101T000000Z-swebench-trial.log"
        trial_log.write_text("ERROR: solver crashed at step 3\nTraceback: ...")

        report = generate_report(
            data, trace=None, trace_id=None, host=None,
            use_llm=False, result_dir=tmp_path,
        )
        assert "Harbor Artifacts" in report
        assert "solver crashed at step 3" in report

    def test_trial_log_with_llm_analysis(self, tmp_path: Path) -> None:
        data = self._result_data(timestamp="20260101T000000Z", benchmark="swebench")
        trial_log = tmp_path / "20260101T000000Z-swebench-trial.log"
        trial_log.write_text("ERROR: solver crashed at step 3")

        with patch("analyze_failure.run_llm_analysis", return_value="The solver crashed due to OOM") as mock_llm:
            report = generate_report(
                data, trace=None, trace_id=None, host=None,
                use_llm=True, result_dir=tmp_path,
            )
        assert "The solver crashed due to OOM" in report
        assert "Diagnosis" in report
        mock_llm.assert_called_once()
        assert "solver crashed at step 3" in mock_llm.call_args[0][0]

    def test_no_trace_no_artifacts(self) -> None:
        data = self._result_data()
        report = generate_report(
            data, trace=None, trace_id=None, host=None,
            use_llm=False, result_dir=None,
        )
        assert "No matching Langfuse trace found" in report

    def test_summary_mode_uses_trial_log(self, tmp_path: Path) -> None:
        data = self._result_data(timestamp="20260101T000000Z", benchmark="swebench")
        trial_log = tmp_path / "20260101T000000Z-swebench-trial.log"
        trial_log.write_text("ERROR: solver timeout")

        with patch("analyze_failure.run_llm_summary", return_value="Solver timed out") as mock_summary:
            report = generate_report(
                data, trace=None, trace_id=None, host=None,
                use_llm=True, summary=True, result_dir=tmp_path,
            )
        assert report == "Solver timed out"
        mock_summary.assert_called_once()
        assert "solver timeout" in mock_summary.call_args[0][0]


class TestFindTrialLog:
    def test_finds_matching_trial_log(self, tmp_path: Path) -> None:
        log_file = tmp_path / "20260101T000000Z-swebench-trial.log"
        log_file.write_text("log content here")
        result = _find_trial_log(tmp_path, {"timestamp": "20260101T000000Z", "benchmark": "swebench"})
        assert result == "log content here"

    def test_truncates_large_log(self, tmp_path: Path) -> None:
        log_file = tmp_path / "20260101T000000Z-swebench-trial.log"
        content = "x" * (60 * 1024)
        log_file.write_text(content)
        result = _find_trial_log(tmp_path, {"timestamp": "20260101T000000Z", "benchmark": "swebench"})
        assert len(result) == 50 * 1024

    def test_returns_empty_when_no_dir(self) -> None:
        result = _find_trial_log(None, {"timestamp": "20260101T000000Z", "benchmark": "swebench"})
        assert result == ""

    def test_returns_empty_when_file_missing(self, tmp_path: Path) -> None:
        result = _find_trial_log(tmp_path, {"timestamp": "20260101T000000Z", "benchmark": "swebench"})
        assert result == ""


# ---------------------------------------------------------------------------
# Additional coverage tests for telemetry module
# ---------------------------------------------------------------------------

class TestIsEnabledInitFails:
    """Cover lines 43-45: Langfuse IS available but constructor raises."""

    def test_returns_false_when_langfuse_init_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        monkeypatch.setattr(telemetry_mod, "_HAS_LANGFUSE", True)
        monkeypatch.setattr(
            telemetry_mod, "Langfuse",
            MagicMock(side_effect=RuntimeError("connection refused")),
            raising=False,
        )
        assert telemetry_mod.is_enabled() is False
        assert telemetry_mod._client is None


class TestGetClient:
    """Cover line 50: _get_client raises when not initialised."""

    def test_raises_when_not_initialised(self) -> None:
        telemetry_mod._client = None
        with pytest.raises(RuntimeError, match="Langfuse not initialised"):
            telemetry_mod._get_client()


class TestSetTraceNameOnSpan:
    """Cover lines 60-70: OTel span attribute setting."""

    def test_sets_trace_name_and_input(self) -> None:
        mock_otel_span = MagicMock()
        mock_otel_span.is_recording.return_value = True
        mock_obs = MagicMock()
        mock_obs._otel_span = mock_otel_span

        mock_attrs = MagicMock()
        mock_attrs.TRACE_NAME = "langfuse.trace.name"
        mock_attrs.TRACE_INPUT = "langfuse.trace.input"

        with patch(
            "factory.telemetry.LangfuseOtelSpanAttributes",
            mock_attrs,
            create=True,
        ):
            # Patch the import inside the function
            with patch.dict("sys.modules", {
                "langfuse._client.attributes": MagicMock(
                    LangfuseOtelSpanAttributes=mock_attrs,
                ),
            }):
                telemetry_mod._set_trace_name_on_span(mock_obs, "my-trace", {"key": "val"})

        mock_otel_span.set_attribute.assert_any_call("langfuse.trace.name", "my-trace")
        mock_otel_span.set_attribute.assert_any_call(
            "langfuse.trace.input", '{"key": "val"}',
        )

    def test_sets_string_input_directly(self) -> None:
        mock_otel_span = MagicMock()
        mock_otel_span.is_recording.return_value = True
        mock_obs = MagicMock()
        mock_obs._otel_span = mock_otel_span

        mock_attrs = MagicMock()
        mock_attrs.TRACE_NAME = "langfuse.trace.name"
        mock_attrs.TRACE_INPUT = "langfuse.trace.input"

        with patch.dict("sys.modules", {
            "langfuse._client.attributes": MagicMock(
                LangfuseOtelSpanAttributes=mock_attrs,
            ),
        }):
            telemetry_mod._set_trace_name_on_span(mock_obs, "my-trace", "raw string input")

        mock_otel_span.set_attribute.assert_any_call("langfuse.trace.input", "raw string input")

    def test_skips_when_no_otel_span(self) -> None:
        mock_obs = MagicMock(spec=[])  # no _otel_span attribute
        with patch.dict("sys.modules", {
            "langfuse._client.attributes": MagicMock(),
        }):
            # Should not raise
            telemetry_mod._set_trace_name_on_span(mock_obs, "name")

    def test_skips_when_not_recording(self) -> None:
        mock_otel_span = MagicMock()
        mock_otel_span.is_recording.return_value = False
        mock_obs = MagicMock()
        mock_obs._otel_span = mock_otel_span

        with patch.dict("sys.modules", {
            "langfuse._client.attributes": MagicMock(),
        }):
            telemetry_mod._set_trace_name_on_span(mock_obs, "name")

        mock_otel_span.set_attribute.assert_not_called()

    def test_handles_import_error_gracefully(self) -> None:
        mock_obs = MagicMock()
        # Remove the module so import fails inside the function
        with patch.dict("sys.modules", {"langfuse._client.attributes": None}):
            # Should not raise
            telemetry_mod._set_trace_name_on_span(mock_obs, "name")


class TestBeginTraceDisabled:
    """Cover line 80: begin_trace returns None when disabled."""

    def test_returns_none_when_disabled(self) -> None:
        telemetry_mod._client = None
        with patch.object(telemetry_mod, "_HAS_LANGFUSE", False):
            assert telemetry_mod.begin_trace("proj", "c1") is None


class TestBeginSpanBranches:
    """Cover lines 112, 127, 136: begin_span edge cases."""

    def test_returns_none_when_disabled(self) -> None:
        telemetry_mod._client = None
        with patch.object(telemetry_mod, "_HAS_LANGFUSE", False):
            assert telemetry_mod.begin_span("t1", "p1", "builder") is None

    def test_with_trace_context_and_parent_span_id(self) -> None:
        """Line 127: parent_span_id provided but not in _observations."""
        mock_client = MagicMock()
        mock_obs = MagicMock()
        mock_obs.id = "span-tc"
        mock_obs.trace_id = "trace-tc"
        mock_client.start_observation.return_value = mock_obs
        telemetry_mod._client = mock_client
        # parent_span_id given but NOT in _observations => falls to elif trace_id
        result = telemetry_mod.begin_span("trace-tc", "missing-parent", "qa")
        assert result == "span-tc"
        call_kwargs = mock_client.start_observation.call_args[1]
        assert call_kwargs["trace_context"] == {
            "trace_id": "trace-tc",
            "parent_span_id": "missing-parent",
        }

    def test_with_no_trace_id_and_no_parent(self) -> None:
        """Line 136: no parent obs, empty trace_id."""
        mock_client = MagicMock()
        mock_obs = MagicMock()
        mock_obs.id = "span-bare"
        mock_obs.trace_id = "trace-bare"
        mock_client.start_observation.return_value = mock_obs
        telemetry_mod._client = mock_client

        result = telemetry_mod.begin_span("", None, "researcher", task="do stuff")
        assert result == "span-bare"
        mock_client.start_observation.assert_called_once_with(
            name="agent:researcher",
            as_type="span",
            input="do stuff",
            metadata={"role": "researcher", "model": None},
        )


class TestEndSpanBranches:
    """Cover lines 159, 162: end_span edge cases."""

    def test_noop_when_disabled(self) -> None:
        telemetry_mod._client = None
        with patch.object(telemetry_mod, "_HAS_LANGFUSE", False):
            telemetry_mod.end_span("t1", "s1")  # should not raise

    def test_noop_when_empty_span_id(self) -> None:
        telemetry_mod._client = MagicMock()
        telemetry_mod.end_span("t1", "")  # should not raise

    def test_noop_when_span_not_found(self) -> None:
        telemetry_mod._client = MagicMock()
        telemetry_mod.end_span("t1", "nonexistent")  # should not raise

    def test_usage_from_object_attrs(self) -> None:
        """Usage as an object with attributes instead of dict."""
        mock_obs = MagicMock()
        telemetry_mod._client = MagicMock()
        telemetry_mod._observations["s1"] = mock_obs

        class UsageObj:
            input_tokens = 200
            output_tokens = 100
            cache_read_tokens = 50
            total_cost_usd = 0.1
            duration_ms = 500.0
            num_turns = 3
            model = "opus"

        telemetry_mod.end_span("t1", "s1", usage=UsageObj())
        meta = mock_obs.update.call_args[1]["metadata"]
        assert meta["input_tokens"] == 200
        assert meta["model"] == "opus"


class TestEndTraceBranches:
    """Cover lines 186, 189->193: end_trace edge cases."""

    def test_noop_when_disabled(self) -> None:
        telemetry_mod._client = None
        with patch.object(telemetry_mod, "_HAS_LANGFUSE", False):
            telemetry_mod.end_trace("t1")  # should not raise

    def test_obs_not_found(self) -> None:
        """Line 189->193: obs is None, should just log."""
        telemetry_mod._client = MagicMock()
        telemetry_mod.end_trace("t1", span_id="nonexistent")  # should not raise

    def test_with_custom_output(self) -> None:
        mock_obs = MagicMock()
        telemetry_mod._client = MagicMock()
        telemetry_mod._observations["s1"] = mock_obs
        telemetry_mod.end_trace("t1", span_id="s1", output="done!")
        mock_obs.update.assert_called_once_with(output="done!")


class TestFindTranscriptFallback:
    """Cover lines 223->229, 225->224, 228: fallback directory search."""

    def test_finds_transcript_via_fallback_search(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        claude_dir = tmp_path / "claude-config" / "projects"
        # Put the transcript in a differently-named dir
        other_dir = claude_dir / "some-other-project-dir"
        other_dir.mkdir(parents=True)
        transcript_file = other_dir / "sess-fallback.jsonl"
        transcript_file.write_text('{"type":"user"}\n')

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
        project_path = tmp_path / "my-project"

        result = telemetry_mod._find_transcript("sess-fallback", project_path)
        assert result == transcript_file

    def test_returns_none_when_not_found_anywhere(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        claude_dir = tmp_path / "claude-config" / "projects"
        claude_dir.mkdir(parents=True)

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
        project_path = tmp_path / "my-project"

        result = telemetry_mod._find_transcript("nonexistent-session", project_path)
        assert result is None


class TestProcessTranscriptItem:
    """Cover _process_transcript_item for various item types."""

    def _make_parent(self) -> MagicMock:
        parent = MagicMock()
        tool_obs = MagicMock()
        parent.start_observation.return_value = tool_obs
        return parent

    def test_user_string_content(self) -> None:
        """Line 254: content part is a raw string."""
        parent = self._make_parent()
        item = {"type": "user", "message": {"content": ["hello world"]}}
        pending: dict[str, Any] = {}
        count = telemetry_mod._process_transcript_item(item, parent, pending)
        assert count == 1
        parent.create_event.assert_called_once_with(name="user_message", input="hello world")

    def test_user_text_type_part(self) -> None:
        """Line 269->252: text type dict in user content."""
        parent = self._make_parent()
        item = {"type": "user", "message": {"content": [
            {"type": "text", "text": "some text"},
        ]}}
        pending: dict[str, Any] = {}
        count = telemetry_mod._process_transcript_item(item, parent, pending)
        assert count == 1
        parent.create_event.assert_called_once_with(name="user_message", input="some text")

    def test_user_tool_result_not_in_pending(self) -> None:
        """Lines 280-285: tool_result with tool_use_id not in pending_tools."""
        parent = self._make_parent()
        item = {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "orphan-id", "content": ["result data"]},
        ]}}
        pending: dict[str, Any] = {}
        count = telemetry_mod._process_transcript_item(item, parent, pending)
        assert count == 1
        parent.create_event.assert_called_once_with(
            name="tool_output",
            output="result data",
            metadata={"tool_use_id": "orphan-id"},
        )

    def test_user_tool_result_with_list_content(self) -> None:
        """Tool result content is a list."""
        parent = self._make_parent()
        tool_obs = MagicMock()
        pending = {"tu-1": tool_obs}
        item = {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu-1", "content": ["part1", "part2"]},
        ]}}
        count = telemetry_mod._process_transcript_item(item, parent, pending)
        assert count == 1
        tool_obs.update.assert_called_once_with(output="part1part2")
        tool_obs.end.assert_called_once()
        assert "tu-1" not in pending

    def test_user_empty_text_ignored(self) -> None:
        """Lines 289->355: text parts present but empty => no event."""
        parent = self._make_parent()
        item = {"type": "user", "message": {"content": [
            {"type": "text", "text": "   "},
        ]}}
        pending: dict[str, Any] = {}
        count = telemetry_mod._process_transcript_item(item, parent, pending)
        assert count == 0
        parent.create_event.assert_not_called()

    def test_assistant_non_dict_content_skipped(self) -> None:
        """Line 301: non-dict content parts are skipped."""
        parent = self._make_parent()
        item = {"type": "assistant", "message": {"content": [
            "raw string part",
            {"type": "text", "text": "real text"},
        ]}}
        pending: dict[str, Any] = {}
        count = telemetry_mod._process_transcript_item(item, parent, pending)
        assert count == 1
        parent.create_event.assert_called_once_with(name="assistant_message", output="real text")

    def test_assistant_empty_text_skipped(self) -> None:
        """Lines 305->299: empty text in assistant content."""
        parent = self._make_parent()
        item = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "  "},
        ]}}
        pending: dict[str, Any] = {}
        count = telemetry_mod._process_transcript_item(item, parent, pending)
        assert count == 0

    def test_assistant_tool_use_no_id(self) -> None:
        """Line 323: tool_use with empty id => ends immediately."""
        parent = self._make_parent()
        tool_obs = MagicMock()
        parent.start_observation.return_value = tool_obs
        item = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"cmd": "ls"}, "id": ""},
        ]}}
        pending: dict[str, Any] = {}
        count = telemetry_mod._process_transcript_item(item, parent, pending)
        assert count == 1
        tool_obs.end.assert_called_once()
        assert len(pending) == 0

    def test_assistant_thinking_type(self) -> None:
        """Lines 325-332: thinking content type."""
        parent = self._make_parent()
        item = {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "Let me think about this..."},
        ]}}
        pending: dict[str, Any] = {}
        count = telemetry_mod._process_transcript_item(item, parent, pending)
        assert count == 1
        parent.create_event.assert_called_once_with(
            name="thinking", output="Let me think about this...",
        )

    def test_assistant_thinking_empty_skipped(self) -> None:
        parent = self._make_parent()
        item = {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "   "},
        ]}}
        pending: dict[str, Any] = {}
        count = telemetry_mod._process_transcript_item(item, parent, pending)
        assert count == 0

    def test_tool_result_type_with_pending(self) -> None:
        """Lines 334-353: top-level tool_result item type with matching pending."""
        parent = self._make_parent()
        tool_obs = MagicMock()
        pending = {"tu-2": tool_obs}
        item = {
            "type": "tool_result",
            "tool_use_id": "tu-2",
            "content": [{"type": "text", "text": "output here"}],
        }
        count = telemetry_mod._process_transcript_item(item, parent, pending)
        assert count == 1
        tool_obs.update.assert_called_once_with(output="output here")
        tool_obs.end.assert_called_once()

    def test_tool_result_type_without_pending(self) -> None:
        """Lines 348-352: top-level tool_result with no matching pending."""
        parent = self._make_parent()
        pending: dict[str, Any] = {}
        item = {
            "type": "tool_result",
            "tool_use_id": "tu-orphan",
            "content": ["string content"],
        }
        count = telemetry_mod._process_transcript_item(item, parent, pending)
        assert count == 1
        parent.create_event.assert_called_once_with(name="tool_output", output="string content")

    def test_tool_result_type_empty_text_ignored(self) -> None:
        """tool_result with empty text."""
        parent = self._make_parent()
        pending: dict[str, Any] = {}
        item = {
            "type": "tool_result",
            "tool_use_id": "tu-x",
            "content": [{"type": "text", "text": "   "}],
        }
        count = telemetry_mod._process_transcript_item(item, parent, pending)
        assert count == 0

    def test_unknown_type_returns_zero(self) -> None:
        parent = self._make_parent()
        pending: dict[str, Any] = {}
        count = telemetry_mod._process_transcript_item(
            {"type": "system"}, parent, pending,
        )
        assert count == 0


class TestIngestTranscriptEdgeCases:
    """Cover lines 370, 379-380, 389, 392-393, 397-398."""

    def test_returns_false_when_disabled(self, tmp_path: Path) -> None:
        """Line 370."""
        telemetry_mod._client = None
        with patch.object(telemetry_mod, "_HAS_LANGFUSE", False):
            assert telemetry_mod.ingest_transcript_to_span(
                "t1", "s1", "sess", tmp_path,
            ) is False

    def test_returns_false_when_parent_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lines 379-380."""
        telemetry_mod._client = MagicMock()
        # Create a transcript file so _find_transcript succeeds
        claude_dir = tmp_path / "claude-config" / "projects"
        dir_name = str(tmp_path.resolve()).replace("/", "-").replace(".", "-")
        transcript_dir = claude_dir / dir_name
        transcript_dir.mkdir(parents=True)
        (transcript_dir / "sess-1.jsonl").write_text('{"type":"user"}\n')
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))

        # _observations does NOT have span-1
        assert telemetry_mod.ingest_transcript_to_span(
            "t1", "span-1", "sess-1", tmp_path,
        ) is False

    def test_handles_empty_lines_and_bad_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lines 389, 392-393: empty lines and JSON decode errors."""
        telemetry_mod._client = MagicMock()
        mock_parent = MagicMock()
        telemetry_mod._observations["s1"] = mock_parent

        claude_dir = tmp_path / "claude-config" / "projects"
        dir_name = str(tmp_path.resolve()).replace("/", "-").replace(".", "-")
        transcript_dir = claude_dir / dir_name
        transcript_dir.mkdir(parents=True)
        transcript_file = transcript_dir / "sess-bad.jsonl"
        transcript_file.write_text("\n\n{not valid json}\n\n")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))

        result = telemetry_mod.ingest_transcript_to_span(
            "t1", "s1", "sess-bad", tmp_path,
        )
        assert result is False  # no observations created

    def test_cleans_up_pending_tools(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lines 397-398: leftover pending tools get ended."""
        telemetry_mod._client = MagicMock()
        mock_parent = MagicMock()
        mock_tool_obs = MagicMock()
        mock_parent.start_observation.return_value = mock_tool_obs
        telemetry_mod._observations["s1"] = mock_parent

        claude_dir = tmp_path / "claude-config" / "projects"
        dir_name = str(tmp_path.resolve()).replace("/", "-").replace(".", "-")
        transcript_dir = claude_dir / dir_name
        transcript_dir.mkdir(parents=True)
        transcript_file = transcript_dir / "sess-pending.jsonl"
        # Tool use with no matching result
        items = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {}, "id": "tu-999"},
            ]}},
        ]
        transcript_file.write_text(
            "\n".join(json.dumps(i) for i in items) + "\n",
        )
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))

        result = telemetry_mod.ingest_transcript_to_span(
            "t1", "s1", "sess-pending", tmp_path,
        )
        assert result is True
        mock_tool_obs.update.assert_called_with(metadata={"status": "no_result"})
        mock_tool_obs.end.assert_called_once()


class TestFindRecentTranscript:
    """Cover line 421: no candidates after session_start."""

    def test_returns_none_when_no_recent_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
        claude_dir = tmp_path / "claude-config" / "projects"
        dir_name = str(tmp_path.resolve()).replace("/", "-").replace(".", "-")
        proj_dir = claude_dir / dir_name
        proj_dir.mkdir(parents=True)
        # Create a file but set session_start far in the future
        old_file = proj_dir / "old-session.jsonl"
        old_file.write_text("{}\n")
        result = telemetry_mod._find_recent_transcript(tmp_path, _time.time() + 9999)
        assert result is None

    def test_returns_most_recent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
        claude_dir = tmp_path / "claude-config" / "projects"
        dir_name = str(tmp_path.resolve()).replace("/", "-").replace(".", "-")
        proj_dir = claude_dir / dir_name
        proj_dir.mkdir(parents=True)

        session_start = _time.time() - 10
        f1 = proj_dir / "sess-a.jsonl"
        f2 = proj_dir / "sess-b.jsonl"
        f1.write_text("{}\n")
        _time.sleep(0.05)
        f2.write_text("{}\n")

        result = telemetry_mod._find_recent_transcript(tmp_path, session_start)
        assert result == f2

    def test_returns_none_when_dir_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
        result = telemetry_mod._find_recent_transcript(tmp_path, 0.0)
        assert result is None


class TestTranscriptTailer:
    """Cover TranscriptTailer: start, stop_and_drain, _run, _ingest_new_lines."""

    def _make_tailer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        *, on_line: Any = None,
    ) -> telemetry_mod.TranscriptTailer:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
        telemetry_mod._client = MagicMock()
        mock_parent = MagicMock()
        telemetry_mod._observations["span-tailer"] = mock_parent

        tailer = telemetry_mod.TranscriptTailer(
            trace_id="trace-tailer",
            span_id="span-tailer",
            project_path=tmp_path,
            session_start=_time.time() - 10,
            on_line=on_line,
        )
        # Use very short intervals for tests
        tailer.POLL_INTERVAL = 0.05
        tailer.FIND_TIMEOUT = 0.5
        tailer.FIND_INTERVAL = 0.05
        return tailer

    def _create_transcript(self, tmp_path: Path, lines: list[str]) -> Path:
        claude_dir = tmp_path / "claude-config" / "projects"
        dir_name = str(tmp_path.resolve()).replace("/", "-").replace(".", "-")
        proj_dir = claude_dir / dir_name
        proj_dir.mkdir(parents=True, exist_ok=True)
        transcript_file = proj_dir / "tailer-sess.jsonl"
        transcript_file.write_text("\n".join(lines) + "\n")
        return transcript_file

    def test_start_and_stop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Lines 476-477, 483-484: basic start/stop lifecycle."""
        items = [
            json.dumps({"type": "user", "message": {"content": ["hello"]}}),
        ]
        self._create_transcript(tmp_path, items)
        tailer = self._make_tailer(tmp_path, monkeypatch)
        tailer.start()
        _time.sleep(0.3)
        count = tailer.stop_and_drain()
        assert count >= 1

    def test_stop_without_start(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """stop_and_drain when thread was never started."""
        tailer = self._make_tailer(tmp_path, monkeypatch)
        count = tailer.stop_and_drain()
        assert count == 0

    def test_stop_drains_pending_tools(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lines 483-484: pending tools cleaned up on stop."""
        tailer = self._make_tailer(tmp_path, monkeypatch)
        mock_tool = MagicMock()
        tailer._pending_tools["tu-left"] = mock_tool
        tailer.stop_and_drain()
        mock_tool.update.assert_called_with(metadata={"status": "no_result"})
        mock_tool.end.assert_called_once()

    def test_stop_handles_pending_tool_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lines 483-484: exception during pending tool cleanup."""
        tailer = self._make_tailer(tmp_path, monkeypatch)
        mock_tool = MagicMock()
        mock_tool.update.side_effect = RuntimeError("boom")
        tailer._pending_tools["tu-err"] = mock_tool
        # Should not raise
        tailer.stop_and_drain()

    def test_stop_handles_final_drain_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lines 476-477: exception during final drain."""
        tailer = self._make_tailer(tmp_path, monkeypatch)
        transcript_path = self._create_transcript(tmp_path, ['{"type":"user"}'])
        tailer._transcript_path = transcript_path

        with patch.object(tailer, "_ingest_new_lines", side_effect=RuntimeError("drain fail")):
            count = tailer.stop_and_drain()
        assert count == 0

    def test_run_transcript_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lines 500-501: transcript never appears within timeout."""
        tailer = self._make_tailer(tmp_path, monkeypatch)
        tailer.FIND_TIMEOUT = 0.1
        tailer.start()
        _time.sleep(0.3)
        count = tailer.stop_and_drain()
        assert count == 0

    def test_ingest_with_on_line_callback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lines 530, 535-536: on_line callback and empty lines."""
        collected: list[bytes] = []
        items = [
            json.dumps({"type": "user", "message": {"content": ["hi"]}}),
            "",  # empty line
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "hello"},
            ]}}),
        ]
        self._create_transcript(tmp_path, items)
        tailer = self._make_tailer(tmp_path, monkeypatch, on_line=collected.append)
        tailer.start()
        _time.sleep(0.3)
        count = tailer.stop_and_drain()
        assert count >= 2
        assert len(collected) >= 2
        assert all(isinstance(b, bytes) for b in collected)

    def test_on_line_exception_handled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lines 535-536: on_line raises."""

        def bad_callback(data: bytes) -> None:
            raise ValueError("callback error")

        items = [json.dumps({"type": "user", "message": {"content": ["hi"]}})]
        self._create_transcript(tmp_path, items)
        tailer = self._make_tailer(tmp_path, monkeypatch, on_line=bad_callback)
        tailer.start()
        _time.sleep(0.3)
        count = tailer.stop_and_drain()
        # Should still ingest despite callback error
        assert count >= 1

    def test_ingest_json_decode_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lines 543-544: bad JSON in transcript."""
        items = [
            "{invalid json!!!",
            json.dumps({"type": "user", "message": {"content": ["valid"]}}),
        ]
        self._create_transcript(tmp_path, items)
        tailer = self._make_tailer(tmp_path, monkeypatch)
        tailer.start()
        _time.sleep(0.3)
        count = tailer.stop_and_drain()
        assert count >= 1  # the valid line

    def test_ingest_item_processing_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lines 549-550: _process_transcript_item raises."""
        items = [json.dumps({"type": "user", "message": {"content": ["hi"]}})]
        self._create_transcript(tmp_path, items)
        tailer = self._make_tailer(tmp_path, monkeypatch)

        with patch.object(
            telemetry_mod, "_process_transcript_item",
            side_effect=RuntimeError("process error"),
        ):
            tailer.start()
            _time.sleep(0.3)
            count = tailer.stop_and_drain()
        assert count == 0

    def test_ingest_no_parent_span(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Line 538: parent is None => skip processing."""
        items = [json.dumps({"type": "user", "message": {"content": ["hi"]}})]
        self._create_transcript(tmp_path, items)
        tailer = self._make_tailer(tmp_path, monkeypatch)
        # Remove the parent span from observations
        telemetry_mod._observations.pop("span-tailer", None)
        tailer.start()
        _time.sleep(0.3)
        count = tailer.stop_and_drain()
        assert count == 0

    def test_run_ingest_exception_in_loop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lines 507-508: exception during _ingest_new_lines in run loop."""
        items = [json.dumps({"type": "user", "message": {"content": ["hi"]}})]
        self._create_transcript(tmp_path, items)
        tailer = self._make_tailer(tmp_path, monkeypatch)

        call_count = 0
        original_ingest = tailer._ingest_new_lines

        def failing_ingest() -> None:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("ingest error")
            original_ingest()

        tailer._ingest_new_lines = failing_ingest  # type: ignore[assignment]
        tailer.start()
        _time.sleep(0.5)
        tailer.stop_and_drain()
        assert call_count >= 2  # confirms it retried after error
