"""Runner abstraction layer for CLI backends (claude, bob, etc.)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from factory.runners._stream import should_stream, stream_subprocess
from factory.runners.bob import BobRunner, is_dry_run
from factory.runners.claude import ClaudeRunner
from factory.runners.protocol import Runner

__all__ = [
    "Runner",
    "ClaudeRunner",
    "BobRunner",
    "get_runner",
    "RunnerName",
    "is_dry_run",
    "should_stream",
    "stream_subprocess",
]

RunnerName = Literal["claude", "bob"]

_RUNNERS: dict[str, type[Runner]] = {
    "claude": ClaudeRunner,  # type: ignore[dict-item]
    "bob": BobRunner,  # type: ignore[dict-item]
}


def get_runner(name: str | None = None, project_path: Path | None = None) -> Runner:
    """Get a runner by name.

    Resolution order:
    1. Explicit name argument
    2. FACTORY_RUNNER environment variable
    3. Default to "claude"

    Args:
        name: Runner name ("claude" or "bob").
        project_path: Path to the project. Passed to BobRunner for cycle state lookup.

    Raises:
        ValueError: If the runner name is not recognized.
    """
    resolved = name or os.environ.get("FACTORY_RUNNER", "claude")
    resolved = resolved.lower().strip()

    if resolved not in _RUNNERS:
        available = ", ".join(_RUNNERS.keys())
        raise ValueError(f"Unknown runner '{resolved}'. Available: {available}")

    if resolved == "bob":
        return BobRunner(project_path=project_path)
    return _RUNNERS[resolved]()


def register_runner(name: str, runner_class: type[Runner]) -> None:
    """Register a runner implementation (used by bob module on import)."""
    _RUNNERS[name] = runner_class
