"""Runner abstraction layer for CLI backends (claude, bob, etc.)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from factory.runners._stream import should_stream, stream_subprocess
from factory.runners.abstraction import AgentRunner, Request, Response
from factory.runners.bob import BobRunner, is_dry_run
from factory.runners.claude import ClaudeRunner
from factory.runners.codex import CodexRunner, is_codex_dry_run
from factory.runners.protocol import Runner

__all__ = [
    "AgentRunner",
    "BobRunner",
    "ClaudeRunner",
    "CodexRunner",
    "Request",
    "Response",
    "Runner",
    "RunnerName",
    "get_runner",
    "is_codex_dry_run",
    "is_dry_run",
    "should_stream",
    "stream_subprocess",
]

RunnerName = Literal["claude", "bob", "codex"]

_RUNNERS: dict[str, type[Runner]] = {
    "claude": ClaudeRunner,  # type: ignore[dict-item]
    "bob": BobRunner,  # type: ignore[dict-item]
    "codex": CodexRunner,  # type: ignore[dict-item]
}


def get_runner(name: str | None = None, project_path: Path | None = None) -> Runner:
    """Get a runner by name.

    Resolution order:
    1. Explicit name argument
    2. FACTORY_RUNNER environment variable
    3. Default to "claude"

    Args:
        name: Runner name ("claude", "bob", or "codex").
        project_path: Path to the project. Passed to BobRunner for cycle state lookup.

    Raises:
        ValueError: If the runner name is not recognized.
    """
    from factory.user_config import resolve

    resolved = resolve("runner", cli_value=name, env_var="FACTORY_RUNNER", default="claude") or "claude"
    resolved = resolved.lower().strip()

    if resolved not in _RUNNERS:
        available = ", ".join(_RUNNERS.keys())
        raise ValueError(f"Unknown runner '{resolved}'. Available: {available}")

    if resolved == "bob":
        return BobRunner(project_path=project_path)  # type: ignore[return-value]
    return _RUNNERS[resolved]()


def register_runner(name: str, runner_class: type[Runner]) -> None:
    """Register a runner implementation (used by bob module on import)."""
    _RUNNERS[name] = runner_class
