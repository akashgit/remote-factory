from __future__ import annotations

import json

import pytest
from opentelemetry.trace import StatusCode

from factory_tracing.spans import (
    trace_factory_cycle,
    trace_agent_invocation,
    record_agent_result,
)


def test_trace_agent_invocation_creates_span_with_correct_name(test_provider):
    _, exporter = test_provider

    with trace_agent_invocation("researcher"):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "invoke_agent researcher"


def test_trace_agent_invocation_sets_required_attributes(test_provider):
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
    assert attrs["langfuse.session.id"] == "run-5"
    assert json.loads(attrs["langfuse.trace.tags"]) == ["builder"]


def test_agent_span_is_child_of_cycle_span(test_provider):
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


def test_multiple_parallel_agents_share_parent(test_provider):
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


def test_record_agent_result_sets_usage(test_provider):
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


def test_record_agent_result_error_status(test_provider):
    _, exporter = test_provider

    with trace_agent_invocation("builder") as span:
        record_agent_result(span, exit_code=1)

    finished = exporter.get_finished_spans()[0]
    assert finished.status.status_code == StatusCode.ERROR


def test_trace_factory_cycle_sets_attributes(test_provider):
    _, exporter = test_provider

    with trace_factory_cycle("run-42", "acme", "fix"):
        pass

    span = exporter.get_finished_spans()[0]
    attrs = dict(span.attributes)
    assert attrs["factory.run.id"] == "run-42"
    assert attrs["factory.project.name"] == "acme"
    assert attrs["factory.mode"] == "fix"
    assert json.loads(attrs["langfuse.trace.tags"]) == ["fix"]


def test_langfuse_observation_type_is_set(test_provider):
    _, exporter = test_provider

    with trace_factory_cycle("run-1", "proj", "improve"):
        with trace_agent_invocation("researcher"):
            pass

    spans = exporter.get_finished_spans()
    for span in spans:
        assert span.attributes["langfuse.observation.type"] == "span"


def test_factory_cycle_session_id_defaults_to_run_id(test_provider):
    _, exporter = test_provider

    with trace_factory_cycle("run-abc", "proj", "improve"):
        pass

    span = exporter.get_finished_spans()[0]
    assert span.attributes["langfuse.session.id"] == "run-abc"


def test_factory_cycle_session_id_override(test_provider):
    _, exporter = test_provider

    with trace_factory_cycle("run-1", "proj", "improve", session_id="session-99"):
        pass

    span = exporter.get_finished_spans()[0]
    assert span.attributes["langfuse.session.id"] == "session-99"


def test_record_agent_result_omits_zero_usage(test_provider):
    _, exporter = test_provider

    with trace_agent_invocation("builder") as span:
        record_agent_result(span, exit_code=0)

    finished = exporter.get_finished_spans()[0]
    attrs = dict(finished.attributes)
    assert "gen_ai.usage.input_tokens" not in attrs
    assert "gen_ai.usage.output_tokens" not in attrs
    assert "gen_ai.usage.cost" not in attrs


def test_agent_invocation_yields_span(test_provider):
    _, exporter = test_provider

    with trace_agent_invocation("builder") as span:
        span.set_attribute("custom.key", "custom-value")

    finished = exporter.get_finished_spans()[0]
    assert finished.attributes["custom.key"] == "custom-value"


def test_all_spans_share_same_trace_id(test_provider):
    _, exporter = test_provider

    with trace_factory_cycle("run-1", "proj", "improve"):
        with trace_agent_invocation("researcher"):
            pass
        with trace_agent_invocation("builder"):
            pass

    spans = exporter.get_finished_spans()
    trace_ids = {s.context.trace_id for s in spans}
    assert len(trace_ids) == 1


def test_record_agent_result_ok_status_on_zero_exit(test_provider):
    _, exporter = test_provider

    with trace_agent_invocation("builder") as span:
        record_agent_result(span, exit_code=0)

    finished = exporter.get_finished_spans()[0]
    assert finished.status.status_code == StatusCode.OK
