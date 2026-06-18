import os

import pytest
from opentelemetry.trace import Tracer

from factory_tracing.config import TracingConfig
from factory_tracing.provider import get_tracer_provider, get_tracer, _reset_provider


@pytest.fixture(autouse=True)
def clean_provider():
    _reset_provider()
    yield
    _reset_provider()


class TestSingleton:
    def test_returns_same_instance(self, monkeypatch):
        monkeypatch.setenv("FACTORY_TRACING_ENABLED", "1")
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

        provider1 = get_tracer_provider()
        provider2 = get_tracer_provider()
        assert provider1 is provider2
        assert provider1 is not None

    def test_singleton_ignores_subsequent_config(self, monkeypatch):
        monkeypatch.setenv("FACTORY_TRACING_ENABLED", "1")
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

        provider1 = get_tracer_provider()
        config2 = TracingConfig(
            enabled=True,
            langfuse_host="http://other:3000",
            langfuse_public_key="pk-other",
            langfuse_secret_key="sk-other",
            otlp_endpoint=None,
            service_name="other-service",
        )
        provider2 = get_tracer_provider(config2)
        assert provider1 is provider2


class TestNoOp:
    def test_returns_none_when_disabled(self, monkeypatch):
        monkeypatch.delenv("FACTORY_TRACING_ENABLED", raising=False)
        provider = get_tracer_provider()
        assert provider is None

    def test_returns_none_when_disabled_explicit_false(self, monkeypatch):
        monkeypatch.setenv("FACTORY_TRACING_ENABLED", "0")
        provider = get_tracer_provider()
        assert provider is None

    def test_get_tracer_returns_noop_tracer_when_disabled(self, monkeypatch):
        monkeypatch.delenv("FACTORY_TRACING_ENABLED", raising=False)
        tracer = get_tracer()
        assert isinstance(tracer, Tracer)


class TestTracerCreation:
    def test_creates_valid_tracer(self, monkeypatch):
        monkeypatch.setenv("FACTORY_TRACING_ENABLED", "1")
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

        tracer = get_tracer("test-tracer")
        assert tracer is not None

    def test_provider_has_correct_service_name(self, monkeypatch):
        monkeypatch.setenv("FACTORY_TRACING_ENABLED", "1")
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

        provider = get_tracer_provider()
        resource = provider.resource
        attrs = dict(resource.attributes)
        assert attrs["service.name"] == "factory-orchestrator"


class TestOTLPEndpoint:
    def test_default_endpoint_from_langfuse_host(self, monkeypatch):
        config = TracingConfig(
            enabled=True,
            langfuse_host="http://langfuse.example.com",
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
            otlp_endpoint=None,
            service_name="factory-orchestrator",
        )
        provider = get_tracer_provider(config)
        assert provider is not None

    def test_explicit_otlp_endpoint_override(self, monkeypatch):
        _reset_provider()
        config = TracingConfig(
            enabled=True,
            langfuse_host="http://langfuse.example.com",
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
            otlp_endpoint="http://custom-collector:4318/v1/traces",
            service_name="factory-orchestrator",
        )
        provider = get_tracer_provider(config)
        assert provider is not None

    def test_custom_service_name(self, monkeypatch):
        _reset_provider()
        config = TracingConfig(
            enabled=True,
            langfuse_host="http://localhost:3000",
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
            otlp_endpoint=None,
            service_name="my-custom-service",
        )
        provider = get_tracer_provider(config)
        resource = provider.resource
        attrs = dict(resource.attributes)
        assert attrs["service.name"] == "my-custom-service"
