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
        enabled_raw = os.environ.get("FACTORY_TRACING_ENABLED", "")
        enabled = enabled_raw.lower() in ("1", "true", "yes")

        langfuse_host = os.environ.get("LANGFUSE_HOST", "")
        langfuse_public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        langfuse_secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        service_name = os.environ.get("OTEL_SERVICE_NAME", "factory-orchestrator")

        return cls(
            enabled=enabled,
            langfuse_host=langfuse_host,
            langfuse_public_key=langfuse_public_key,
            langfuse_secret_key=langfuse_secret_key,
            otlp_endpoint=otlp_endpoint,
            service_name=service_name,
        )
