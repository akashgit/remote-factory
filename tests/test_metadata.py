"""Tests for Phase 5: experiment attribution and metadata enrichment."""
from __future__ import annotations

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

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


def test_experiment_attributes_set_on_cycle_span(integration_with_exporter):
    integration, exporter = integration_with_exporter

    with integration.start_cycle(
        "run-1", "proj", "improve",
        experiment_id="exp-42",
        hypothesis_id="hyp-7",
        hypothesis_category="FIX",
    ):
        pass

    spans = exporter.get_finished_spans()
    cycle = next(s for s in spans if s.name == "factory.cycle")
    attrs = _attrs(cycle)

    assert attrs["factory.experiment.id"] == "exp-42"
    assert attrs["factory.hypothesis.id"] == "hyp-7"
    assert attrs["factory.hypothesis.category"] == "FIX"


def test_langfuse_metadata_attributes_use_correct_prefix(integration_with_exporter):
    integration, exporter = integration_with_exporter

    with integration.start_cycle(
        "run-1", "proj", "improve",
        experiment_id="exp-42",
        hypothesis_category="EXPLORE",
    ):
        pass

    spans = exporter.get_finished_spans()
    cycle = next(s for s in spans if s.name == "factory.cycle")
    attrs = _attrs(cycle)

    assert attrs["langfuse.trace.metadata.experiment_id"] == "exp-42"
    assert attrs["langfuse.trace.metadata.hypothesis_category"] == "EXPLORE"


def test_session_id_overridden_by_experiment_id(integration_with_exporter):
    integration, exporter = integration_with_exporter

    with integration.start_cycle(
        "run-1", "proj", "improve",
        experiment_id="exp-42",
    ):
        pass

    spans = exporter.get_finished_spans()
    cycle = next(s for s in spans if s.name == "factory.cycle")
    attrs = _attrs(cycle)

    assert attrs["langfuse.session.id"] == "exp-42"


def test_eval_result_recorded_as_event(integration_with_exporter):
    integration, exporter = integration_with_exporter

    with integration.start_cycle("run-1", "proj", "improve") as span:
        integration.record_eval_result(
            span,
            {"tests": 0.8, "lint": 0.9, "capability_surface": 0.5},
        )

    spans = exporter.get_finished_spans()
    cycle = next(s for s in spans if s.name == "factory.cycle")

    assert len(cycle.events) == 1
    event = cycle.events[0]
    assert event.name == "eval.result"
    event_attrs = dict(event.attributes)
    assert event_attrs["eval.tests"] == 0.8
    assert event_attrs["eval.lint"] == 0.9
    assert event_attrs["eval.capability_surface"] == 0.5


def test_experiment_verdict_sets_attributes(integration_with_exporter):
    integration, exporter = integration_with_exporter

    with integration.start_cycle("run-1", "proj", "improve") as span:
        integration.record_experiment_verdict(span, "ACCEPTED", 0.85)

    spans = exporter.get_finished_spans()
    cycle = next(s for s in spans if s.name == "factory.cycle")
    attrs = _attrs(cycle)

    assert attrs["factory.experiment.verdict"] == "ACCEPTED"
    assert attrs["factory.experiment.composite_score"] == 0.85


def test_omitted_optional_params_dont_set_empty_attributes(integration_with_exporter):
    integration, exporter = integration_with_exporter

    with integration.start_cycle("run-1", "proj", "improve"):
        pass

    spans = exporter.get_finished_spans()
    cycle = next(s for s in spans if s.name == "factory.cycle")
    attrs = _attrs(cycle)

    assert "factory.experiment.id" not in attrs
    assert "factory.hypothesis.id" not in attrs
    assert "factory.hypothesis.category" not in attrs
    assert "langfuse.trace.metadata.experiment_id" not in attrs
    assert "langfuse.trace.metadata.hypothesis_category" not in attrs


def test_noop_when_disabled(disabled_config, monkeypatch):
    monkeypatch.delenv("FACTORY_TRACING_ENABLED", raising=False)
    _provider_mod._reset_provider()
    try:
        integration = TracingIntegration(disabled_config)
        assert not integration.enabled

        with integration.start_cycle(
            "run-1", "proj", "improve",
            experiment_id="exp-1",
            hypothesis_id="hyp-1",
            hypothesis_category="FIX",
        ) as span:
            integration.record_eval_result(span, {"tests": 0.8})
            integration.record_experiment_verdict(span, "ACCEPTED", 0.9)
    finally:
        _provider_mod._reset_provider()
