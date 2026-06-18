"""TracerProvider singleton with OTLP HTTP exporter for Langfuse."""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

if TYPE_CHECKING:
    from factory_tracing.config import TracingConfig

logger = logging.getLogger(__name__)

_provider: TracerProvider | None = None


def get_provider(config: TracingConfig) -> TracerProvider:
    global _provider
    if _provider is not None:
        return _provider

    resource = Resource.create({"service.name": config.service_name})
    _provider = TracerProvider(resource=resource)

    if config.otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            auth = base64.b64encode(
                f"{config.langfuse_public_key}:{config.langfuse_secret_key}".encode()
            ).decode()
            exporter = OTLPSpanExporter(
                endpoint=config.otlp_endpoint,
                headers={"Authorization": f"Basic {auth}"},
            )
            _provider.add_span_processor(SimpleSpanProcessor(exporter))
            logger.info("OTLP exporter configured: %s", config.otlp_endpoint)
        except Exception:
            logger.exception("Failed to configure OTLP exporter")

    trace.set_tracer_provider(_provider)
    return _provider


def get_tracer(name: str = "factory") -> trace.Tracer:
    return trace.get_tracer(name)


def shutdown() -> None:
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None


def reset_provider() -> None:
    """Reset the singleton for testing."""
    global _provider
    _provider = None
