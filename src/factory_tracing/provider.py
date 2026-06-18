from __future__ import annotations

import base64
import threading

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import Tracer

from .config import TracingConfig

_provider: TracerProvider | None = None
_lock = threading.Lock()


def get_tracer_provider(config: TracingConfig | None = None) -> TracerProvider | None:
    global _provider

    if _provider is not None:
        return _provider

    with _lock:
        if _provider is not None:
            return _provider

        if config is None:
            config = TracingConfig.from_env()

        if not config.enabled:
            return None

        resource = Resource.create({"service.name": config.service_name})

        endpoint = config.otlp_endpoint
        if not endpoint:
            endpoint = f"{config.langfuse_host}/api/public/otel/v1/traces"

        auth_string = base64.b64encode(
            f"{config.langfuse_public_key}:{config.langfuse_secret_key}".encode()
        ).decode()

        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            headers={
                "Authorization": f"Basic {auth_string}",
                "x-langfuse-ingestion-version": "4",
            },
        )

        provider = TracerProvider(resource=resource)
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        _provider = provider
        return _provider


def get_tracer(name: str = "factory-tracing") -> Tracer:
    provider = get_tracer_provider()
    if provider is None:
        from opentelemetry.trace import get_tracer as otel_get_tracer
        return otel_get_tracer(name)
    return provider.get_tracer(name)


def shutdown_tracing() -> None:
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None


def _reset_provider() -> None:
    """Reset the singleton for testing purposes only."""
    global _provider
    _provider = None
