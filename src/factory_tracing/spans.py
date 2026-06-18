from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from opentelemetry.trace import Span, StatusCode

from .provider import get_tracer


@contextmanager
def trace_factory_cycle(
    run_id: str,
    project_name: str,
    mode: str,
) -> Iterator[Span]:
    """Root span for a full CEO improvement cycle."""
    tracer = get_tracer()
    with tracer.start_as_current_span("factory.cycle") as span:
        span.set_attribute("factory.run.id", run_id)
        span.set_attribute("factory.project.name", project_name)
        span.set_attribute("factory.mode", mode)
        span.set_attribute("langfuse.observation.type", "span")
        span.set_attribute("langfuse.session.id", run_id)
        try:
            yield span
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise
        else:
            if span.status.status_code == StatusCode.ERROR:
                return
            span.set_status(StatusCode.OK)


@contextmanager
def trace_agent_invocation(
    role: str,
    task_summary: str,
    run_id: str,
    project_name: str,
    model: str = "anthropic",
) -> Iterator[Span]:
    """Child span for a single agent invocation within a cycle."""
    tracer = get_tracer()
    with tracer.start_as_current_span(f"invoke_agent {role}") as span:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.agent.name", role)
        span.set_attribute("gen_ai.system", "anthropic")
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("factory.run.id", run_id)
        span.set_attribute("factory.project.name", project_name)
        span.set_attribute("factory.task.summary", task_summary)
        if task_summary:
            span.set_attribute("gen_ai.prompt", task_summary)
        span.set_attribute("langfuse.observation.type", "span")
        span.set_attribute("langfuse.session.id", run_id)
        span.set_attribute("langfuse.trace.tags", (role,))
        try:
            yield span
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise
        else:
            if span.status.status_code == StatusCode.ERROR:
                return
            span.set_status(StatusCode.OK)


def record_agent_result(
    span: Span,
    exit_code: int,
    duration_ms: float = 0.0,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    response_text: str | None = None,
    start_time: float | None = None,
    model: str | None = None,
) -> None:
    """Record subprocess result and optional usage metrics on an agent span."""
    if start_time is not None:
        duration_ms = (time.monotonic() - start_time) * 1000.0

    span.set_attribute("subprocess.returncode", exit_code)
    span.set_attribute("subprocess.duration_ms", duration_ms)

    if response_text is not None:
        span.set_attribute("gen_ai.completion", response_text)

    if model is not None:
        span.set_attribute("gen_ai.request.model", model)

    if input_tokens is not None:
        span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    if output_tokens is not None:
        span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    if cost_usd is not None:
        span.set_attribute("gen_ai.usage.cost", cost_usd)

    if exit_code != 0:
        span.set_status(StatusCode.ERROR, f"agent exited with code {exit_code}")
    else:
        span.set_status(StatusCode.OK)
