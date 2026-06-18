"""Tracing configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class TracingConfig:
    enabled: bool
    langfuse_host: str
    langfuse_public_key: str
    langfuse_secret_key: str
    otlp_endpoint: str | None
    service_name: str

    @classmethod
    def from_env(cls) -> TracingConfig:
        enabled = os.environ.get("FACTORY_TRACING_ENABLED", "0") == "1"
        langfuse_host = os.environ.get(
            "LANGFUSE_HOST",
            os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000"),
        )
        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if not otlp_endpoint and langfuse_host:
            otlp_endpoint = f"{langfuse_host.rstrip('/')}/api/public/otel/v1/traces"

        return cls(
            enabled=enabled,
            langfuse_host=langfuse_host,
            langfuse_public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
            langfuse_secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
            otlp_endpoint=otlp_endpoint,
            service_name=os.environ.get("OTEL_SERVICE_NAME", "factory"),
        )


def get_max_content_length() -> int:
    return int(os.environ.get("FACTORY_TRACING_MAX_CONTENT", "32000"))
