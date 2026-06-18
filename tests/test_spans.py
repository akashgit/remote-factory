"""Tests for factory_tracing.spans — span hierarchy, attributes, and error status."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from factory_tracing import provider as _provider_mod
from factory_tracing.spans import (
    record_agent_result,
    trace_agent_invocation,
    trace_factory_cycle,
)


@pytest.fixture(autouse=True)
def tracing_setup():
    """Set up InMemorySpanExporter and tear down the provider singleton after each test."""
    exporter = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exporter))
    _provider_mod._provider = tp
    yield exporter
    tp.shutdown()
    _provider_mod._provider = None


def _attrs(span) -> dict:
    return dict(span.attributes) if span.attributes else {}


# --- trace_factory_cycle ---


def test_cycle_creates_root_span(tracing_setup):
    with trace_factory_cycle("run-1", "my-project", "improve"):
        pass
    spans = tracing_setup.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "factory.cycle"


def test_cycle_sets_factory_attributes(tracing_setup):
    with trace_factory_cycle("run-42", "acme", "build"):
        pass
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert attrs["factory.run.id"] == "run-42"
    assert attrs["factory.project.name"] == "acme"
    assert attrs["factory.mode"] == "build"


def test_cycle_sets_langfuse_attributes(tracing_setup):
    with trace_factory_cycle("run-1", "proj", "improve"):
        pass
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert attrs["langfuse.observation.type"] == "span"
    assert attrs["langfuse.session.id"] == "run-1"


def test_cycle_status_ok_on_success(tracing_setup):
    with trace_factory_cycle("r", "p", "m"):
        pass
    assert tracing_setup.get_finished_spans()[0].status.status_code == StatusCode.OK


def test_cycle_status_error_on_exception(tracing_setup):
    with pytest.raises(ValueError, match="boom"):
        with trace_factory_cycle("r", "p", "m"):
            raise ValueError("boom")
    span = tracing_setup.get_finished_spans()[0]
    assert span.status.status_code == StatusCode.ERROR
    assert "boom" in span.status.description


# --- trace_agent_invocation ---


def test_agent_span_name_includes_role(tracing_setup):
    with trace_agent_invocation("researcher", "find bugs", "run-1", "proj"):
        pass
    spans = tracing_setup.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "invoke_agent researcher"


def test_agent_sets_gen_ai_attributes(tracing_setup):
    with trace_agent_invocation("builder", "implement feature", "run-1", "proj"):
        pass
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert attrs["gen_ai.operation.name"] == "invoke_agent"
    assert attrs["gen_ai.agent.name"] == "builder"
    assert attrs["gen_ai.system"] == "anthropic"


def test_agent_sets_factory_and_langfuse_attributes(tracing_setup):
    with trace_agent_invocation("reviewer", "review code", "run-5", "acme"):
        pass
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert attrs["factory.run.id"] == "run-5"
    assert attrs["factory.project.name"] == "acme"
    assert attrs["factory.task.summary"] == "review code"
    assert attrs["langfuse.observation.type"] == "span"
    assert attrs["langfuse.session.id"] == "run-5"


def test_agent_langfuse_tags_contains_role(tracing_setup):
    with trace_agent_invocation("strategist", "plan", "r", "p"):
        pass
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    tags = attrs["langfuse.trace.tags"]
    assert "strategist" in tags


def test_agent_status_error_on_exception(tracing_setup):
    with pytest.raises(RuntimeError):
        with trace_agent_invocation("builder", "task", "r", "p"):
            raise RuntimeError("fail")
    span = tracing_setup.get_finished_spans()[0]
    assert span.status.status_code == StatusCode.ERROR


# --- span hierarchy ---


def test_agent_is_child_of_cycle(tracing_setup):
    with trace_factory_cycle("run-1", "proj", "improve"):
        with trace_agent_invocation("researcher", "search", "run-1", "proj"):
            pass
    spans = tracing_setup.get_finished_spans()
    assert len(spans) == 2
    agent_span = next(s for s in spans if s.name.startswith("invoke_agent"))
    cycle_span = next(s for s in spans if s.name == "factory.cycle")
    assert agent_span.parent is not None
    assert agent_span.parent.span_id == cycle_span.context.span_id
    assert agent_span.context.trace_id == cycle_span.context.trace_id


def test_multiple_agents_share_same_parent(tracing_setup):
    with trace_factory_cycle("run-1", "proj", "improve"):
        with trace_agent_invocation("researcher", "search", "run-1", "proj"):
            pass
        with trace_agent_invocation("builder", "build", "run-1", "proj"):
            pass
    spans = tracing_setup.get_finished_spans()
    assert len(spans) == 3
    cycle_span = next(s for s in spans if s.name == "factory.cycle")
    agent_spans = [s for s in spans if s.name.startswith("invoke_agent")]
    assert len(agent_spans) == 2
    for agent_span in agent_spans:
        assert agent_span.parent.span_id == cycle_span.context.span_id


def test_all_spans_share_same_trace_id(tracing_setup):
    with trace_factory_cycle("run-1", "proj", "improve"):
        with trace_agent_invocation("researcher", "search", "run-1", "proj"):
            pass
        with trace_agent_invocation("builder", "build", "run-1", "proj"):
            pass
    spans = tracing_setup.get_finished_spans()
    trace_ids = {s.context.trace_id for s in spans}
    assert len(trace_ids) == 1


# --- record_agent_result ---


def test_record_sets_exit_code_and_duration(tracing_setup):
    with trace_agent_invocation("builder", "task", "r", "p") as span:
        record_agent_result(span, exit_code=0, duration_ms=1500.0)
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert attrs["subprocess.returncode"] == 0
    assert attrs["subprocess.duration_ms"] == 1500.0


def test_record_sets_usage_when_provided(tracing_setup):
    with trace_agent_invocation("builder", "task", "r", "p") as span:
        record_agent_result(
            span,
            exit_code=0,
            duration_ms=2000.0,
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.05,
        )
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert attrs["gen_ai.usage.input_tokens"] == 1000
    assert attrs["gen_ai.usage.output_tokens"] == 500
    assert attrs["gen_ai.usage.cost"] == 0.05


def test_record_omits_usage_when_not_provided(tracing_setup):
    with trace_agent_invocation("builder", "task", "r", "p") as span:
        record_agent_result(span, exit_code=0, duration_ms=100.0)
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert "gen_ai.usage.input_tokens" not in attrs
    assert "gen_ai.usage.output_tokens" not in attrs
    assert "gen_ai.usage.cost" not in attrs


def test_record_error_status_on_nonzero_exit(tracing_setup):
    with trace_agent_invocation("builder", "task", "r", "p") as span:
        record_agent_result(span, exit_code=1, duration_ms=500.0)
    span_data = tracing_setup.get_finished_spans()[0]
    assert span_data.status.status_code == StatusCode.ERROR


def test_record_ok_status_on_zero_exit(tracing_setup):
    with trace_agent_invocation("builder", "task", "r", "p") as span:
        record_agent_result(span, exit_code=0, duration_ms=500.0)
    span_data = tracing_setup.get_finished_spans()[0]
    assert span_data.status.status_code == StatusCode.OK


# --- gen_ai.prompt attribute ---


def test_agent_sets_gen_ai_prompt(tracing_setup):
    with trace_agent_invocation("builder", "implement feature X", "r", "p"):
        pass
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert attrs["gen_ai.prompt"] == "implement feature X"


def test_agent_no_gen_ai_prompt_when_empty(tracing_setup):
    with trace_agent_invocation("builder", "", "r", "p"):
        pass
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert "gen_ai.prompt" not in attrs


# --- gen_ai.request.model attribute ---


def test_agent_sets_default_model(tracing_setup):
    with trace_agent_invocation("builder", "task", "r", "p"):
        pass
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert attrs["gen_ai.request.model"] == "anthropic"


def test_agent_sets_custom_model(tracing_setup):
    with trace_agent_invocation("builder", "task", "r", "p", model="claude-sonnet-4-6"):
        pass
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert attrs["gen_ai.request.model"] == "claude-sonnet-4-6"


# --- gen_ai.completion attribute ---


def test_record_sets_completion(tracing_setup):
    with trace_agent_invocation("builder", "task", "r", "p") as span:
        record_agent_result(span, exit_code=0, response_text="VERIFIED")
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert attrs["gen_ai.completion"] == "VERIFIED"


def test_record_omits_completion_when_not_provided(tracing_setup):
    with trace_agent_invocation("builder", "task", "r", "p") as span:
        record_agent_result(span, exit_code=0, duration_ms=100.0)
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert "gen_ai.completion" not in attrs


# --- duration calculation from start_time ---


def test_record_calculates_duration_from_start_time(tracing_setup):
    with trace_agent_invocation("builder", "task", "r", "p") as span:
        fake_start = time.monotonic() - 1.5
        record_agent_result(span, exit_code=0, start_time=fake_start)
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert attrs["subprocess.duration_ms"] >= 1400.0
    assert attrs["subprocess.duration_ms"] < 3000.0


def test_record_start_time_overrides_duration_ms(tracing_setup):
    with trace_agent_invocation("builder", "task", "r", "p") as span:
        fake_start = time.monotonic() - 2.0
        record_agent_result(span, exit_code=0, duration_ms=999.0, start_time=fake_start)
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert attrs["subprocess.duration_ms"] >= 1900.0


# --- model override via record_agent_result ---


def test_record_overrides_model(tracing_setup):
    with trace_agent_invocation("builder", "task", "r", "p") as span:
        record_agent_result(span, exit_code=0, duration_ms=100.0, model="claude-opus-4-6")
    attrs = _attrs(tracing_setup.get_finished_spans()[0])
    assert attrs["gen_ai.request.model"] == "claude-opus-4-6"
