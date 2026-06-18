"""Trace context propagation for subprocess environments."""

from __future__ import annotations

import os

from opentelemetry import context, trace
from opentelemetry.trace import format_span_id, format_trace_id


def build_traced_env(
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build an environment dict with TRACEPARENT for W3C trace context propagation."""
    env = dict(base_env) if base_env else dict(os.environ)

    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.trace_id != 0:
        traceparent = (
            f"00-{format_trace_id(ctx.trace_id)}-{format_span_id(ctx.span_id)}-"
            f"{'01' if ctx.trace_flags & 1 else '00'}"
        )
        env["TRACEPARENT"] = traceparent

    return env
