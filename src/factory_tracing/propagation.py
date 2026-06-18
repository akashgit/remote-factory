from __future__ import annotations

import base64
import os

from opentelemetry import propagate

from .config import TracingConfig
from .provider import get_tracer_provider


def build_traced_env(base_env: dict | None = None) -> dict:
    env = dict(base_env if base_env is not None else os.environ)

    provider = get_tracer_provider()
    if provider is None:
        return env

    carrier: dict[str, str] = {}
    propagate.inject(carrier)

    if "traceparent" in carrier:
        env["TRACEPARENT"] = carrier["traceparent"]
    if "tracestate" in carrier:
        env["TRACESTATE"] = carrier["tracestate"]

    config = TracingConfig.from_env()

    langfuse_host = config.langfuse_host.rstrip("/")
    auth_string = base64.b64encode(
        f"{config.langfuse_public_key}:{config.langfuse_secret_key}".encode()
    ).decode()

    env["OTEL_SERVICE_NAME"] = "factory-agent"

    return env
