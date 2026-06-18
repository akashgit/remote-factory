"""TracingIntegration facade for easy setup and agent execution."""

from __future__ import annotations

import logging

from factory_tracing.config import TracingConfig
from factory_tracing.executor import AgentResult, run_traced_agent
from factory_tracing.provider import get_provider, shutdown

logger = logging.getLogger(__name__)


class TracingIntegration:
    """High-level facade for factory tracing.

    Usage:
        tracing = TracingIntegration.from_env()
        if tracing.enabled:
            result = tracing.run_agent(prompt="...", role="builder", ...)
            tracing.shutdown()
    """

    def __init__(self, config: TracingConfig) -> None:
        self.config = config
        self._initialized = False

    @classmethod
    def from_env(cls) -> TracingIntegration:
        config = TracingConfig.from_env()
        return cls(config)

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def initialize(self) -> None:
        if self._initialized:
            return
        if self.config.enabled:
            get_provider(self.config)
            self._initialized = True
            logger.info("Tracing initialized: %s", self.config.otlp_endpoint)

    def run_agent(
        self,
        prompt: str,
        role: str,
        run_id: str,
        project_name: str,
        system_prompt: str | None = None,
        cwd: str | None = None,
        model: str = "anthropic",
        env: dict | None = None,
    ) -> AgentResult:
        self.initialize()
        return run_traced_agent(
            prompt=prompt,
            role=role,
            run_id=run_id,
            project_name=project_name,
            system_prompt=system_prompt,
            cwd=cwd,
            model=model,
            env=env,
        )

    def shutdown(self) -> None:
        if self._initialized:
            shutdown()
            self._initialized = False
