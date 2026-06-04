"""AgentRunner compositor — composes Agent + Runtime into a unified runner."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from factory.runners.config import AgentLaunchConfig
from factory.runners.runtime import ProcessRuntime, Runtime

if TYPE_CHECKING:
    from factory.models import AgentUsage
    from factory.runners.protocol import Agent, Runner

logger = logging.getLogger(__name__)


class AgentRunner:
    """Composes an Agent (command builder) with a Runtime (executor).

    This is the new unified runner returned by get_runner(). It implements
    the legacy Runner protocol for backwards compatibility.

    Can also wrap a legacy Runner instance directly via from_legacy(),
    delegating headless()/interactive_run() to preserve all runner-specific
    behavior (e.g. bob ceiling tracking, usage logging).
    """

    def __init__(self, agent: "Agent", runtime: Runtime | None = None) -> None:
        self._agent = agent
        self._runtime = runtime or ProcessRuntime()
        self._legacy: "Runner | None" = None

    @classmethod
    def from_legacy(cls, legacy_runner: "Runner") -> "AgentRunner":
        """Wrap a legacy Runner in an AgentRunner shell.

        All headless()/interactive_run() calls delegate directly to the
        legacy runner, preserving its full behavior.
        """
        # Create a minimal Agent just for the name
        instance = cls.__new__(cls)
        instance._agent = None  # type: ignore[assignment]
        instance._runtime = ProcessRuntime()
        instance._legacy = legacy_runner
        return instance

    @property
    def name(self) -> str:
        if self._legacy is not None:
            return self._legacy.name
        return self._agent.name

    def __getattr__(self, name: str) -> object:
        """Proxy attribute access to the legacy runner for backward compatibility.

        This allows tests that access runner-specific attributes (e.g.
        BobRunner.cycle_start) to work through AgentRunner.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        if self._legacy is not None:
            return getattr(self._legacy, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    async def headless(
        self,
        prompt: str,
        task: str,
        cwd: Path,
        *,
        timeout: float = 600.0,
        model: str | None = None,
        dangerously_skip_permissions: bool = True,
        role: str = "unknown",
        session_name: str | None = None,
        tmux_persist: bool = False,
    ) -> tuple[str, int, "AgentUsage | None"]:
        """Run a headless agent invocation.

        Implements the legacy Runner.headless() interface.
        """
        if self._legacy is not None:
            return await self._legacy.headless(
                prompt, task, cwd,
                timeout=timeout, model=model,
                dangerously_skip_permissions=dangerously_skip_permissions,
                role=role, session_name=session_name, tmux_persist=tmux_persist,
            )

        permissions = "permissionless" if dangerously_skip_permissions else "suggest"

        config = AgentLaunchConfig(
            project_path=cwd,
            append_system_prompt=prompt,
            task=task,
            role=role,
            model=model,
            timeout=timeout,
            permissions=permissions,
            session_name=session_name,
        )

        self._agent.preflight()

        cmd = self._agent.get_launch_command(config)
        env = self._agent.get_environment(config)

        stream_prefix = f"[{self._agent.name}:{role}]"

        # Select runtime based on tmux_persist flag
        runtime = self._runtime
        if tmux_persist:
            from factory.runners.runtime import TmuxRuntime
            runtime = TmuxRuntime()

        try:
            stdout, return_code = await runtime.execute(
                cmd, env, cwd,
                timeout=timeout,
                stream_prefix=stream_prefix,
            )

            result = self._agent.parse_output(stdout, return_code)
            return result.output, result.return_code, result.usage
        finally:
            if hasattr(self._agent, "cleanup"):
                self._agent.cleanup()

    def interactive_run(
        self,
        prompt: str,
        task: str,
        cwd: Path,
        *,
        model: str | None = None,
        role: str = "ceo",
        dangerously_skip_permissions: bool = False,
        session_name: str | None = None,
    ) -> int:
        """Run an interactive CLI session.

        Implements the legacy Runner.interactive_run() interface.
        """
        if self._legacy is not None:
            return self._legacy.interactive_run(
                prompt, task, cwd,
                model=model, role=role,
                dangerously_skip_permissions=dangerously_skip_permissions,
                session_name=session_name,
            )

        permissions = "permissionless" if dangerously_skip_permissions else "suggest"

        config = AgentLaunchConfig(
            project_path=cwd,
            append_system_prompt=prompt,
            task=task,
            role=role,
            model=model,
            permissions=permissions,
            session_name=session_name,
            mode="interactive",
        )

        self._agent.preflight()

        cmd = self._agent.get_launch_command(config)
        env = self._agent.get_environment(config)

        try:
            return self._runtime.execute_interactive(cmd, env, cwd)
        finally:
            if hasattr(self._agent, "cleanup"):
                self._agent.cleanup()
