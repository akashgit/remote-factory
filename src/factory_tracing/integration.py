"""High-level tracing integration for the factory orchestrator.

Production code — must NOT import langfuse or dotenv.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from opentelemetry.trace import Span

from .config import TracingConfig
from .executor import AgentResult, run_traced_agent
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
        self._run_id: str = ""
        if self._config.enabled:
            self._provider = get_tracer_provider(self._config)

    @property
    def enabled(self) -> bool:
        return self._provider is not None

    @contextmanager
    def start_cycle(
        self,
        run_id: str,
        project_name: str,
        mode: str,
        experiment_id: str | None = None,
        hypothesis_id: str | None = None,
        hypothesis_category: str | None = None,
    ) -> Iterator[Span | _NoOpSpan]:
        if not self.enabled:
            yield _NOOP_SPAN
            return
        self._run_id = run_id
        with trace_factory_cycle(run_id, project_name, mode) as span:
            if experiment_id is not None:
                span.set_attribute("factory.experiment.id", experiment_id)
                span.set_attribute("langfuse.trace.metadata.experiment_id", experiment_id)
                span.set_attribute("langfuse.session.id", experiment_id)
            if hypothesis_id is not None:
                span.set_attribute("factory.hypothesis.id", hypothesis_id)
            if hypothesis_category is not None:
                span.set_attribute("factory.hypothesis.category", hypothesis_category)
                span.set_attribute("langfuse.trace.metadata.hypothesis_category", hypothesis_category)
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
        response_text: str | None = None,
        start_time: float | None = None,
        model: str | None = None,
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
            response_text=response_text,
            start_time=start_time,
            model=model,
        )

    def set_span_io(
        self,
        span: Span | _NoOpSpan,
        input_text: str | None = None,
        output_text: str | None = None,
    ) -> None:
        if not self.enabled or isinstance(span, _NoOpSpan):
            return
        if input_text is not None:
            span.set_attribute("gen_ai.prompt", input_text)  # type: ignore[union-attr]
        if output_text is not None:
            span.set_attribute("gen_ai.completion", output_text)  # type: ignore[union-attr]

    def record_eval_result(
        self,
        span: Span | _NoOpSpan,
        scores: dict,
    ) -> None:
        if not self.enabled or isinstance(span, _NoOpSpan):
            return
        prefixed = {f"eval.{k}": v for k, v in scores.items()}
        span.add_event("eval.result", attributes=prefixed)  # type: ignore[arg-type]

    def record_experiment_verdict(
        self,
        span: Span | _NoOpSpan,
        verdict: str,
        composite_score: float,
    ) -> None:
        if not self.enabled or isinstance(span, _NoOpSpan):
            return
        span.set_attribute("factory.experiment.verdict", verdict)  # type: ignore[union-attr]
        span.set_attribute("factory.experiment.composite_score", composite_score)  # type: ignore[union-attr]

    def run_agent(
        self,
        prompt: str,
        role: str,
        run_id: str = "",
        project_name: str = "",
        cwd: str | None = None,
        model: str = "anthropic",
    ) -> AgentResult:
        env = self.build_subprocess_env() if self.enabled else None
        return run_traced_agent(
            prompt=prompt,
            role=role,
            run_id=run_id or self._run_id,
            project_name=project_name,
            cwd=cwd,
            model=model,
            env=env,
        )

    def shutdown(self) -> None:
        if self.enabled:
            shutdown_tracing()
            self._provider = None
