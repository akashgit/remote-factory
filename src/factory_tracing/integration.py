"""High-level tracing integration for the factory orchestrator.

Production code — must NOT import langfuse or dotenv.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from opentelemetry.trace import Span

from .config import TracingConfig
from .propagation import build_traced_env
from .provider import get_tracer_provider, shutdown_tracing
from .spans import record_agent_result, trace_agent_invocation, trace_factory_cycle


class _NoOpSpan:
    """Minimal stand-in when tracing is disabled."""

    def set_attribute(self, key: str, value: object) -> None:
        pass

    def set_status(self, *args: object, **kwargs: object) -> None:
        pass

    def add_event(self, name: str, attributes: dict | None = None) -> None:
        pass


_NOOP_SPAN = _NoOpSpan()


class TracingIntegration:
    def __init__(self, config: TracingConfig | None = None) -> None:
        self._config = config or TracingConfig.from_env()
        self._provider = None
        if self._config.enabled:
            self._provider = get_tracer_provider(self._config)

    @property
    def enabled(self) -> bool:
        return self._provider is not None

    @contextmanager
    def start_cycle(
        self, run_id: str, project_name: str, mode: str
    ) -> Iterator[Span | _NoOpSpan]:
        if not self.enabled:
            yield _NOOP_SPAN
            return
        with trace_factory_cycle(run_id, project_name, mode) as span:
            yield span

    @contextmanager
    def start_agent(
        self, role: str, task_summary: str = "", run_id: str = "", project_name: str = ""
    ) -> Iterator[Span | _NoOpSpan]:
        if not self.enabled:
            yield _NOOP_SPAN
            return
        with trace_agent_invocation(role, task_summary, run_id, project_name) as span:
            yield span

    def build_subprocess_env(self, base_env: dict | None = None) -> dict:
        return build_traced_env(base_env)

    def record_agent_result(
        self,
        span: Span | _NoOpSpan,
        exit_code: int,
        duration_ms: float = 0.0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        if not self.enabled or isinstance(span, _NoOpSpan):
            return
        record_agent_result(
            span,  # type: ignore[arg-type]
            exit_code=exit_code,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )

    def shutdown(self) -> None:
        if self.enabled:
            shutdown_tracing()
            self._provider = None
