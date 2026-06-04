"""Runner protocol — interface for CLI backend implementations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from factory.models import AgentUsage
    from factory.runners.config import AgentLaunchConfig


@dataclass
class AgentResult:
    """Result from an agent invocation."""

    output: str
    return_code: int
    usage: "AgentUsage | None" = None


class Agent(Protocol):
    """Protocol for CLI backend implementations (new 4-layer architecture).

    Agent classes are pure-function objects: they build commands, construct
    environments, and parse output — but never execute subprocesses.
    """

    name: str

    def get_launch_command(self, config: "AgentLaunchConfig") -> list[str]:
        """Build the CLI command to launch this agent.

        Args:
            config: The launch configuration.

        Returns:
            A list of command-line arguments.
        """
        ...

    def get_environment(self, config: "AgentLaunchConfig") -> dict[str, str]:
        """Build the subprocess environment for this agent.

        Args:
            config: The launch configuration.

        Returns:
            A dictionary of environment variables.
        """
        ...

    def parse_output(self, stdout: str, return_code: int) -> AgentResult:
        """Parse raw subprocess output into an AgentResult.

        Args:
            stdout: Raw stdout from the subprocess.
            return_code: The subprocess exit code.

        Returns:
            An AgentResult with parsed output and optional usage.
        """
        ...

    def preflight(self) -> None:
        """Run any preflight checks (e.g. auth verification).

        Raises an exception if checks fail.
        """
        ...


class Runner(Protocol):
    """Legacy protocol for CLI backend implementations (claude, bob, etc.).

    Kept as a backwards-compatibility alias. New code should use Agent + Runtime.
    """

    name: str

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
        """Run a headless (non-interactive) agent invocation."""
        ...

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
        """Run an interactive CLI session as a subprocess (returns on exit)."""
        ...
