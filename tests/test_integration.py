"""Tests for factory_tracing.integration — TracingIntegration lifecycle."""
from __future__ import annotations

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from factory_tracing import provider as _provider_mod
from factory_tracing.config import TracingConfig
from factory_tracing.integration import TracingIntegration


@pytest.fixture
def enabled_config():
    return TracingConfig(
        enabled=True,
        langfuse_host="http://localhost:3000",
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
        otlp_endpoint=None,
        service_name="test-service",
    )


@pytest.fixture
def disabled_config():
    return TracingConfig(
        enabled=False,
        langfuse_host="",
        langfuse_public_key="",
        langfuse_secret_key="",
        otlp_endpoint=None,
        service_name="test-service",
    )


@pytest.fixture
def integration_with_exporter(enabled_config):
    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "test-service"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    old = _provider_mod._provider
    _provider_mod._provider = provider

    integration = TracingIntegration(enabled_config)

    yield integration, exporter

    _provider_mod._provider = old
    exporter.clear()
    provider.shutdown()


def _attrs(span) -> dict:
    return dict(span.attributes) if span.attributes else {}


class TestTracingIntegrationEnabled:
    def test_lifecycle_cycle_and_agents(self, integration_with_exporter):
        integration, exporter = integration_with_exporter

        with integration.start_cycle("run-1", "test-project", "improve") as cycle_span:
            with integration.start_agent("researcher", "search code", "run-1", "test-project") as agent1:
                integration.record_agent_result(agent1, exit_code=0, duration_ms=1200.0)

            with integration.start_agent("builder", "implement fix", "run-1", "test-project") as agent2:
                integration.record_agent_result(agent2, exit_code=0, duration_ms=2400.0, input_tokens=500, output_tokens=200)

        spans = exporter.get_finished_spans()
        assert len(spans) == 3

        cycle_span_data = next(s for s in spans if s.name == "factory.cycle")
        agent_spans = [s for s in spans if s.name.startswith("invoke_agent")]
        assert len(agent_spans) == 2

        for agent_span in agent_spans:
            assert agent_span.parent is not None
            assert agent_span.parent.span_id == cycle_span_data.context.span_id
            assert agent_span.context.trace_id == cycle_span_data.context.trace_id

    def test_agent_spans_have_correct_names(self, integration_with_exporter):
        integration, exporter = integration_with_exporter

        with integration.start_cycle("run-1", "proj", "build"):
            with integration.start_agent("researcher", "search", "run-1", "proj"):
                pass
            with integration.start_agent("builder", "build", "run-1", "proj"):
                pass

        spans = exporter.get_finished_spans()
        names = {s.name for s in spans}
        assert "invoke_agent researcher" in names
        assert "invoke_agent builder" in names

    def test_record_result_sets_attributes(self, integration_with_exporter):
        integration, exporter = integration_with_exporter

        with integration.start_cycle("run-1", "proj", "build"):
            with integration.start_agent("builder", "task", "run-1", "proj") as span:
                integration.record_agent_result(
                    span, exit_code=0, duration_ms=1500.0,
                    input_tokens=100, output_tokens=50, cost_usd=0.01,
                )

        agent_span = next(
            s for s in exporter.get_finished_spans()
            if s.name.startswith("invoke_agent")
        )
        attrs = _attrs(agent_span)
        assert attrs["subprocess.returncode"] == 0
        assert attrs["subprocess.duration_ms"] == 1500.0
        assert attrs["gen_ai.usage.input_tokens"] == 100
        assert attrs["gen_ai.usage.output_tokens"] == 50

    def test_all_spans_share_trace_id(self, integration_with_exporter):
        integration, exporter = integration_with_exporter

        with integration.start_cycle("run-1", "proj", "build"):
            with integration.start_agent("researcher", "search", "run-1", "proj"):
                pass
            with integration.start_agent("builder", "build", "run-1", "proj"):
                pass

        spans = exporter.get_finished_spans()
        trace_ids = {s.context.trace_id for s in spans}
        assert len(trace_ids) == 1

    def test_record_result_with_response_text(self, integration_with_exporter):
        integration, exporter = integration_with_exporter

        with integration.start_cycle("run-1", "proj", "build"):
            with integration.start_agent("builder", "task", "run-1", "proj") as span:
                integration.record_agent_result(
                    span, exit_code=0, duration_ms=100.0,
                    response_text="done",
                )

        agent_span = next(
            s for s in exporter.get_finished_spans()
            if s.name.startswith("invoke_agent")
        )
        attrs = _attrs(agent_span)
        assert attrs["gen_ai.completion"] == "done"

    def test_set_span_io(self, integration_with_exporter):
        integration, exporter = integration_with_exporter

        with integration.start_cycle("run-1", "proj", "build"):
            with integration.start_agent("builder", "task", "run-1", "proj") as span:
                integration.set_span_io(span, input_text="hello", output_text="world")

        agent_span = next(
            s for s in exporter.get_finished_spans()
            if s.name.startswith("invoke_agent")
        )
        attrs = _attrs(agent_span)
        assert attrs["gen_ai.prompt"] == "hello"
        assert attrs["gen_ai.completion"] == "world"

    def test_stores_run_id(self, integration_with_exporter):
        integration, exporter = integration_with_exporter
        with integration.start_cycle("run-42", "proj", "build"):
            assert integration._run_id == "run-42"

    def test_build_subprocess_env_delegates(self, integration_with_exporter):
        integration, exporter = integration_with_exporter
        base = {"PATH": "/usr/bin"}
        result = integration.build_subprocess_env(base)
        assert isinstance(result, dict)
        assert result["PATH"] == "/usr/bin"


class TestTracingIntegrationDisabled:
    def test_disabled_returns_noop_spans(self, disabled_config, monkeypatch):
        monkeypatch.delenv("FACTORY_TRACING_ENABLED", raising=False)
        _provider_mod._reset_provider()
        try:
            integration = TracingIntegration(disabled_config)
            assert not integration.enabled

            with integration.start_cycle("run-1", "proj", "build") as span:
                span.set_attribute("test", "value")
                with integration.start_agent("builder", "task", "run-1", "proj") as agent_span:
                    integration.record_agent_result(agent_span, exit_code=0, duration_ms=100.0)
        finally:
            _provider_mod._reset_provider()

    def test_disabled_build_env_passthrough(self, disabled_config, monkeypatch):
        monkeypatch.delenv("FACTORY_TRACING_ENABLED", raising=False)
        _provider_mod._reset_provider()
        try:
            integration = TracingIntegration(disabled_config)
            base = {"PATH": "/usr/bin", "HOME": "/home/test"}
            result = integration.build_subprocess_env(base)
            assert result == base
        finally:
            _provider_mod._reset_provider()

    def test_disabled_shutdown_is_noop(self, disabled_config, monkeypatch):
        monkeypatch.delenv("FACTORY_TRACING_ENABLED", raising=False)
        _provider_mod._reset_provider()
        try:
            integration = TracingIntegration(disabled_config)
            integration.shutdown()
        finally:
            _provider_mod._reset_provider()
