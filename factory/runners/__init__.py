"""Runner abstraction layer for CLI backends (claude, bob, codex)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, get_args

from factory.runners._stream import should_stream, stream_subprocess
from factory.runners.bob import BobRunner, BobShellAgent, is_dry_run
from factory.runners.claude import ClaudeCodeAgent, ClaudeRunner
from factory.runners.codex import CodexAgent, CodexRunner, is_codex_dry_run
from factory.runners.compositor import AgentRunner
from factory.runners.config import AgentLaunchConfig
from factory.runners.protocol import Agent, AgentResult, Runner
from factory.runners.runtime import ProcessRuntime, Runtime, TmuxRuntime

__all__ = [
    "Agent",
    "AgentLaunchConfig",
    "AgentResult",
    "AgentRunner",
    "BobRunner",
    "BobShellAgent",
    "ClaudeCodeAgent",
    "ClaudeRunner",
    "CodexAgent",
    "CodexRunner",
    "ProcessRuntime",
    "Runner",
    "RunnerName",
    "Runtime",
    "TmuxRuntime",
    "get_runner",
    "is_codex_dry_run",
    "is_dry_run",
    "register_runner",
    "should_stream",
    "stream_subprocess",
]

RunnerName = Literal["claude", "bob", "codex"]

RUNNER_CHOICES: tuple[str, ...] = get_args(RunnerName)

_RUNNERS: dict[str, type[Runner]] = {
    "claude": ClaudeRunner,  # type: ignore[dict-item]
    "bob": BobRunner,  # type: ignore[dict-item]
    "codex": CodexRunner,  # type: ignore[dict-item]
}

_AGENTS: dict[str, type] = {
    "claude": ClaudeCodeAgent,
    "bob": BobShellAgent,
    "codex": CodexAgent,
}


def get_runner(name: str | None = None, project_path: Path | None = None) -> AgentRunner:
    """Get an AgentRunner by name.

    Resolution order:
    1. Explicit name argument
    2. FACTORY_RUNNER environment variable
    3. Default to "claude"

    Returns an AgentRunner that implements the legacy Runner protocol.
    For claude/codex, uses the new Agent+Runtime composition.
    For bob, wraps the legacy BobRunner (which has complex ceiling/usage tracking).
    """
    from factory.user_config import resolve

    resolved = resolve("runner", cli_value=name, env_var="FACTORY_RUNNER", default="claude") or "claude"
    resolved = resolved.lower().strip()

    if resolved not in _AGENTS:
        available = ", ".join(_AGENTS.keys())
        raise ValueError(f"Unknown runner '{resolved}'. Available: {available}")

    if resolved == "bob":
        # Bob has complex lifecycle (ceiling tracking, usage logging, key persistence)
        # that lives in BobRunner. Wrap via from_legacy to preserve all behavior.
        return AgentRunner.from_legacy(BobRunner(project_path=project_path))

    return AgentRunner(_AGENTS[resolved]())


def register_runner(name: str, runner_class: type[Runner]) -> None:
    """Register a runner implementation (used by bob module on import)."""
    _RUNNERS[name] = runner_class
