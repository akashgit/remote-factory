"""Tests for provider module — TracerProvider singleton."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from factory_tracing.config import TracingConfig, get_max_content_length
from factory_tracing.provider import get_provider, get_tracer, reset_provider, shutdown
from factory_tracing.spans import set_langfuse_io, set_llm_usage, truncate


class TestProvider:
    def test_get_provider_returns_tracer_provider(self):
        config = TracingConfig(
            enabled=True,
            langfuse_host="http://localhost:3000",
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
            otlp_endpoint=None,
            service_name="test",
        )
        provider = get_provider(config)
        assert isinstance(provider, TracerProvider)

    def test_singleton_returns_same_instance(self):
        config = TracingConfig(
            enabled=True,
            langfuse_host="http://localhost:3000",
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
            otlp_endpoint=None,
            service_name="test",
        )
        p1 = get_provider(config)
        p2 = get_provider(config)
        assert p1 is p2

    def test_get_tracer_returns_tracer(self):
        tracer = get_tracer("test")
        assert tracer is not None

    def test_shutdown_resets_provider(self):
        config = TracingConfig(
            enabled=True,
            langfuse_host="http://localhost:3000",
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
            otlp_endpoint=None,
            service_name="test",
        )
        p1 = get_provider(config)
        shutdown()
        p2 = get_provider(config)
        assert p1 is not p2


class TestConfig:
    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("FACTORY_TRACING_ENABLED", "1")
        monkeypatch.setenv("LANGFUSE_HOST", "http://test:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-abc")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-xyz")
        config = TracingConfig.from_env()
        assert config.enabled is True
        assert config.langfuse_host == "http://test:3000"
        assert config.langfuse_public_key == "pk-abc"
        assert config.langfuse_secret_key == "sk-xyz"
        assert config.otlp_endpoint == "http://test:3000/api/public/otel/v1/traces"

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("FACTORY_TRACING_ENABLED", raising=False)
        config = TracingConfig.from_env()
        assert config.enabled is False

    def test_max_content_length_default(self, monkeypatch):
        monkeypatch.delenv("FACTORY_TRACING_MAX_CONTENT", raising=False)
        assert get_max_content_length() == 32000

    def test_max_content_length_custom(self, monkeypatch):
        monkeypatch.setenv("FACTORY_TRACING_MAX_CONTENT", "1000")
        assert get_max_content_length() == 1000


class TestSpanHelpers:
    def test_truncate_short_string(self, monkeypatch):
        monkeypatch.setenv("FACTORY_TRACING_MAX_CONTENT", "100")
        result = truncate("hello")
        assert result == "hello"

    def test_truncate_long_string(self, monkeypatch):
        monkeypatch.setenv("FACTORY_TRACING_MAX_CONTENT", "10")
        result = truncate("a" * 100)
        assert len(result) < 100
        assert "truncated" in result

    def test_set_llm_usage(self):
        tracer = get_tracer("test")
        with tracer.start_as_current_span("test") as span:
            usage = {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 20,
            }
            set_llm_usage(span, usage)

    def test_set_langfuse_io_with_dict(self):
        tracer = get_tracer("test")
        with tracer.start_as_current_span("test") as span:
            set_langfuse_io(span, {"key": "value"}, {"result": "ok"})

    def test_set_langfuse_io_with_string(self):
        tracer = get_tracer("test")
        with tracer.start_as_current_span("test") as span:
            set_langfuse_io(span, "input text", "output text")

    def test_set_langfuse_io_with_none(self):
        tracer = get_tracer("test")
        with tracer.start_as_current_span("test") as span:
            set_langfuse_io(span, None, None)
