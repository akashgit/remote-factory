from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Iterator

from opentelemetry import trace
from opentelemetry.trace import Span, StatusCode

from .provider import get_tracer


@contextmanager
def trace_factory_cycle(
    run_id: str,
    project_name: str,
    mode: str,
    session_id: str | None = None,
) -> Iterator[Span]:
    tracer = get_tracer("factory-tracing")
    with tracer.start_as_current_span("factory.cycle") as span:
        span.set_attribute("factory.run.id", run_id)
        span.set_attribute("factory.project.name", project_name)
        span.set_attribute("factory.mode", mode)
        span.set_attribute("langfuse.observation.type", "span")
        span.set_attribute("langfuse.session.id", session_id or run_id)
        span.set_attribute("langfuse.trace.tags", json.dumps([mode]))
        try:
            yield span
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise
        else:
            span.set_status(StatusCode.OK)


@contextmanager
def trace_agent_invocation(
    role: str,
    task_summary: str = "",
    run_id: str = "",
    project_name: str = "",
) -> Iterator[Span]:
    tracer = get_tracer("factory-tracing")
    with tracer.start_as_current_span(f"invoke_agent {role}") as span:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.agent.name", role)
        span.set_attribute("gen_ai.system", "anthropic")
        span.set_attribute("factory.run.id", run_id)
        span.set_attribute("factory.project.name", project_name)
        span.set_attribute("langfuse.observation.type", "span")
        span.set_attribute("langfuse.session.id", run_id)
        span.set_attribute("langfuse.trace.tags", json.dumps([role]))
        if task_summary:
            span.set_attribute("factory.task.summary", task_summary)
        try:
            yield span
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise
        else:
            if span.status.status_code != StatusCode.ERROR:
                span.set_status(StatusCode.OK)


def record_agent_result(
    span: Span,
    exit_code: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
) -> None:
    span.set_attribute("subprocess.returncode", exit_code)
    if input_tokens > 0:
        span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    if output_tokens > 0:
        span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    if cost_usd > 0.0:
        span.set_attribute("gen_ai.usage.cost", cost_usd)
    if exit_code != 0:
        span.set_status(StatusCode.ERROR, f"exit code {exit_code}")
