import base64
import os
import re

import pytest
from opentelemetry import trace

from factory_tracing.propagation import build_traced_env


TRACEPARENT_RE = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


class TestBuildTracedEnvWithActiveSpan:
    def test_injects_traceparent(self, test_provider):
        provider, _exporter = test_provider
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test-span"):
            env = build_traced_env(base_env={})
            assert "TRACEPARENT" in env

    def test_traceparent_format(self, test_provider):
        provider, _exporter = test_provider
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test-span"):
            env = build_traced_env(base_env={})
            assert TRACEPARENT_RE.match(env["TRACEPARENT"])

    def test_trace_id_matches_current_span(self, test_provider):
        provider, _exporter = test_provider
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test-span") as span:
            env = build_traced_env(base_env={})
            m = TRACEPARENT_RE.match(env["TRACEPARENT"])
            assert m is not None
            traceparent_trace_id = m.group(1)
            span_trace_id = format(span.get_span_context().trace_id, "032x")
            assert traceparent_trace_id == span_trace_id


class TestClaudeCodeOtelVars:
    def test_claude_code_otel_vars_set(self, test_provider, monkeypatch):
        provider, _exporter = test_provider
        monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.test:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test-span"):
            env = build_traced_env(base_env={})
            assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
            assert env["CLAUDE_CODE_ENHANCED_TELEMETRY_BETA"] == "1"
            assert env["OTEL_TRACES_EXPORTER"] == "otlp"
            assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
            assert env["OTEL_SERVICE_NAME"] == "factory-agent"

    def test_otel_endpoint_set_correctly(self, test_provider, monkeypatch):
        provider, _exporter = test_provider
        monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.test:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test-span"):
            env = build_traced_env(base_env={})
            assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://langfuse.test:3000/api/public/otel"

    def test_auth_header_set(self, test_provider, monkeypatch):
        provider, _exporter = test_provider
        monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.test:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test-span"):
            env = build_traced_env(base_env={})
            expected_b64 = base64.b64encode(b"pk-test:sk-test").decode()
            headers = env["OTEL_EXPORTER_OTLP_HEADERS"]
            assert f"Authorization=Basic {expected_b64}" in headers
            assert "x-langfuse-ingestion-version=4" in headers


class TestNoActiveSpan:
    def test_no_traceparent_without_active_span(self, test_provider, monkeypatch):
        monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.test:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        env = build_traced_env(base_env={})
        assert "TRACEPARENT" not in env


class TestDisabledTracing:
    def test_disabled_tracing_passthrough(self, monkeypatch):
        monkeypatch.delenv("FACTORY_TRACING_ENABLED", raising=False)
        from factory_tracing.provider import _reset_provider
        _reset_provider()
        try:
            base = {"PATH": "/usr/bin", "HOME": "/home/test"}
            env = build_traced_env(base_env=base)
            assert env == base
            assert env is not base
            assert "TRACEPARENT" not in env
            assert "CLAUDE_CODE_ENABLE_TELEMETRY" not in env
        finally:
            _reset_provider()


class TestBaseEnvNone:
    def test_base_env_none_uses_os_environ(self, test_provider, monkeypatch):
        provider, _exporter = test_provider
        monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.test:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        monkeypatch.setenv("MY_CUSTOM_VAR", "test-value")
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test-span"):
            env = build_traced_env(base_env=None)
            assert env["MY_CUSTOM_VAR"] == "test-value"
            assert "TRACEPARENT" in env
