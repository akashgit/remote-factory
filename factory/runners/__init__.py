"""Runner abstraction layer for CLI backends (claude, bob, etc.)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from factory.runners._stream import should_stream, stream_subprocess
from factory.runners.bob import BobRunner, is_dry_run
from factory.runners.cli_adapter import CLIAdapter
from factory.runners.claude import ClaudeRunner
from factory.runners.codex import CodexRunner, is_codex_dry_run
from factory.runners.opencode import OpenCodeRunner
from factory.runners.protocol import Runner
from factory.runners.registry import RunnerRegistry

logger = logging.getLogger(__name__)

# Try importing ACP adapter; skip gracefully if agent-client-protocol not installed
try:
    from factory.runners.acp_adapter import ACPAdapter
    _has_acp = True
except Exception:
    ACPAdapter = None  # type: ignore[assignment,misc]
    _has_acp = False
    logger.debug("ACP adapter not available (agent-client-protocol not installed)")

__all__ = [
    "Runner",
    "CLIAdapter",
    "ACPAdapter",
    "ClaudeRunner",
    "BobRunner",
    "CodexRunner",
    "OpenCodeRunner",
    "RunnerRegistry",
    "get_runner",
    "register_runner",
    "RunnerName",
    "is_dry_run",
    "is_codex_dry_run",
    "should_stream",
    "stream_subprocess",
    "_registry",
]

RunnerName = Literal["claude", "bob", "codex", "opencode"]

# -- Module-level registry instance ------------------------------------------

_registry = RunnerRegistry()

_registry.register("claude", lambda **_kw: ClaudeRunner())
_registry.register("bob", lambda **kw: BobRunner(project_path=kw.get("project_path")))
_registry.register("codex", lambda **_kw: CodexRunner())

_registry.register("opencode", lambda **_kw: OpenCodeRunner())

# -- Legacy dict kept for backward compat (read-only reference) ---------------

_RUNNERS: dict[str, type[Runner]] = {
    "claude": ClaudeRunner,  # type: ignore[dict-item]
    "bob": BobRunner,  # type: ignore[dict-item]
    "codex": CodexRunner,  # type: ignore[dict-item]
}
_RUNNERS["opencode"] = OpenCodeRunner  # type: ignore[assignment]


def get_runner(name: str | None = None, project_path: Path | None = None) -> Runner:
    """Get a runner by name.

    Resolution order:
    1. Explicit name argument
    2. FACTORY_RUNNER environment variable
    3. Default to "claude"

    Args:
        name: Runner name ("claude", "bob", "codex", or "opencode").
        project_path: Path to the project. Passed to BobRunner for cycle state lookup.

    Raises:
        ValueError: If the runner name is not recognized.
    """
    from factory.user_config import resolve

    resolved = resolve("runner", cli_value=name, env_var="FACTORY_RUNNER", default="claude") or "claude"
    resolved = resolved.lower().strip()

    return _registry.get(resolved, project_path=project_path)


def register_runner(name: str, runner_class: type[Runner]) -> None:
    """Register a runner implementation (backward compat)."""
    _RUNNERS[name] = runner_class
    _registry.register(name, runner_class)
