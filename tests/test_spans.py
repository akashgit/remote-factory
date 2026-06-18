from __future__ import annotations

import pytest
from opentelemetry.trace import StatusCode

from factory_tracing.spans import (
    trace_factory_cycle,
    trace_agent_invocation,
    record_agent_result,
)


class TestTraceFactoryCycle:
    def test_creates_span_with_correct_name(self, test_provider):
        _, exporter = test_provider

        with trace_factory_cycle("run-1", "my-project", "improve"):
            pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "factory.cycle"

    def test_sets_required_attributes(self, test_provider):
        _, exporter = test_provider

        with trace_factory_cycle("run-42", "acme", "fix"):
            pass

        span = exporter.get_finished_spans()[0]
        attrs = dict(span.attributes)
        assert attrs["factory.run.id"] == "run-42"
        assert attrs["factory.project.name"] == "acme"
        assert attrs["factory.mode"] == "fix"

    def test_sets_langfuse_observation_type(self, test_provider):
        _, exporter = test_provider

        with trace_factory_cycle("run-1", "proj", "improve"):
            pass

        span = exporter.get_finished_spans()[0]
        assert span.attributes["langfuse.observation.type"] == "span"

    def test_sets_langfuse_session_id(self, test_provider):
        _, exporter = test_provider

        with trace_factory_cycle("run-abc", "proj", "improve"):
            pass

        span = exporter.get_finished_spans()[0]
        assert span.attributes["langfuse.session.id"] == "run-abc"

    def test_status_ok_on_success(self, test_provider):
        _, exporter = test_provider

        with trace_factory_cycle("run-1", "proj", "improve"):
            pass

        span = exporter.get_finished_spans()[0]
        assert span.status.status_code == StatusCode.OK

    def test_status_error_on_exception(self, test_provider):
        _, exporter = test_provider

        with pytest.raises(ValueError, match="boom"):
            with trace_factory_cycle("run-1", "proj", "improve"):
                raise ValueError("boom")

        span = exporter.get_finished_spans()[0]
        assert span.status.status_code == StatusCode.ERROR


class TestTraceAgentInvocation:
    def test_creates_span_with_role_in_name(self, test_provider):
        _, exporter = test_provider

        with trace_agent_invocation("researcher"):
            pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "invoke_agent researcher"

    def test_sets_genai_attributes(self, test_provider):
        _, exporter = test_provider

        with trace_agent_invocation("builder", run_id="run-5", project_name="proj"):
            pass

        span = exporter.get_finished_spans()[0]
        attrs = dict(span.attributes)
        assert attrs["gen_ai.operation.name"] == "invoke_agent"
        assert attrs["gen_ai.agent.name"] == "builder"
        assert attrs["gen_ai.system"] == "anthropic"
        assert attrs["factory.run.id"] == "run-5"
        assert attrs["factory.project.name"] == "proj"

    def test_sets_langfuse_attributes(self, test_provider):
        _, exporter = test_provider

        with trace_agent_invocation("reviewer", run_id="run-7"):
            pass

        span = exporter.get_finished_spans()[0]
        attrs = dict(span.attributes)
        assert attrs["langfuse.observation.type"] == "span"
        assert attrs["langfuse.session.id"] == "run-7"
        assert tuple(attrs["langfuse.trace.tags"]) == ("reviewer",)

    def test_sets_task_summary_when_provided(self, test_provider):
        _, exporter = test_provider

        with trace_agent_invocation("builder", task_summary="fix login bug"):
            pass

        span = exporter.get_finished_spans()[0]
        assert span.attributes["factory.task.summary"] == "fix login bug"

    def test_no_task_summary_when_empty(self, test_provider):
        _, exporter = test_provider

        with trace_agent_invocation("builder"):
            pass

        span = exporter.get_finished_spans()[0]
        assert "factory.task.summary" not in span.attributes

    def test_status_ok_on_success(self, test_provider):
        _, exporter = test_provider

        with trace_agent_invocation("builder"):
            pass

        span = exporter.get_finished_spans()[0]
        assert span.status.status_code == StatusCode.OK

    def test_status_error_on_exception(self, test_provider):
        _, exporter = test_provider

        with pytest.raises(RuntimeError):
            with trace_agent_invocation("builder"):
                raise RuntimeError("fail")

        span = exporter.get_finished_spans()[0]
        assert span.status.status_code == StatusCode.ERROR

    def test_yields_span_for_caller(self, test_provider):
        _, exporter = test_provider

        with trace_agent_invocation("builder") as span:
            span.set_attribute("custom.key", "custom-value")

        finished = exporter.get_finished_spans()[0]
        assert finished.attributes["custom.key"] == "custom-value"


class TestSpanParenting:
    def test_agent_span_is_child_of_cycle_span(self, test_provider):
        _, exporter = test_provider

        with trace_factory_cycle("run-1", "proj", "improve"):
            with trace_agent_invocation("researcher"):
                pass

        spans = exporter.get_finished_spans()
        agent_span = next(s for s in spans if s.name == "invoke_agent researcher")
        cycle_span = next(s for s in spans if s.name == "factory.cycle")

        assert agent_span.parent is not None
        assert agent_span.parent.span_id == cycle_span.context.span_id
        assert agent_span.context.trace_id == cycle_span.context.trace_id

    def test_multiple_agent_spans_share_same_parent(self, test_provider):
        _, exporter = test_provider

        with trace_factory_cycle("run-1", "proj", "improve"):
            with trace_agent_invocation("researcher"):
                pass
            with trace_agent_invocation("builder"):
                pass

        spans = exporter.get_finished_spans()
        cycle_span = next(s for s in spans if s.name == "factory.cycle")
        agent_spans = [s for s in spans if s.name.startswith("invoke_agent")]

        assert len(agent_spans) == 2
        for agent_span in agent_spans:
            assert agent_span.parent.span_id == cycle_span.context.span_id

    def test_all_spans_share_same_trace_id(self, test_provider):
        _, exporter = test_provider

        with trace_factory_cycle("run-1", "proj", "improve"):
            with trace_agent_invocation("researcher"):
                pass
            with trace_agent_invocation("builder"):
                pass

        spans = exporter.get_finished_spans()
        trace_ids = {s.context.trace_id for s in spans}
        assert len(trace_ids) == 1


class TestRecordAgentResult:
    def test_sets_returncode_and_duration(self, test_provider):
        _, exporter = test_provider

        with trace_agent_invocation("builder") as span:
            record_agent_result(span, exit_code=0, duration_ms=1500.0)

        finished = exporter.get_finished_spans()[0]
        assert finished.attributes["subprocess.returncode"] == 0
        assert finished.attributes["subprocess.duration_ms"] == 1500.0

    def test_sets_token_attributes(self, test_provider):
        _, exporter = test_provider

        with trace_agent_invocation("builder") as span:
            record_agent_result(
                span,
                exit_code=0,
                input_tokens=500,
                output_tokens=200,
                cost_usd=0.015,
            )

        finished = exporter.get_finished_spans()[0]
        attrs = dict(finished.attributes)
        assert attrs["gen_ai.usage.input_tokens"] == 500
        assert attrs["gen_ai.usage.output_tokens"] == 200
        assert attrs["gen_ai.usage.cost"] == 0.015

    def test_omits_none_values(self, test_provider):
        _, exporter = test_provider

        with trace_agent_invocation("builder") as span:
            record_agent_result(span, exit_code=0)

        finished = exporter.get_finished_spans()[0]
        attrs = dict(finished.attributes)
        assert "subprocess.duration_ms" not in attrs
        assert "gen_ai.usage.input_tokens" not in attrs
        assert "gen_ai.usage.output_tokens" not in attrs
        assert "gen_ai.usage.cost" not in attrs

    def test_sets_error_status_on_nonzero_exit(self, test_provider):
        _, exporter = test_provider

        with trace_agent_invocation("builder") as span:
            record_agent_result(span, exit_code=1)

        finished = exporter.get_finished_spans()[0]
        assert finished.status.status_code == StatusCode.ERROR

    def test_ok_status_preserved_on_zero_exit(self, test_provider):
        _, exporter = test_provider

        with trace_agent_invocation("builder") as span:
            record_agent_result(span, exit_code=0)

        finished = exporter.get_finished_spans()[0]
        assert finished.status.status_code == StatusCode.OK


class TestInMemoryExporter:
    def test_spans_captured_by_in_memory_exporter(self, test_provider):
        _, exporter = test_provider

        with trace_factory_cycle("run-1", "proj", "improve"):
            with trace_agent_invocation("researcher", run_id="run-1"):
                pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 2
        names = {s.name for s in spans}
        assert "factory.cycle" in names
        assert "invoke_agent researcher" in names
