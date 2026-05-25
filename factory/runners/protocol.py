"""Runner protocol — interface for CLI backend implementations."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Runner(Protocol):
    """Protocol for CLI backend implementations (claude, bob, etc.)."""

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
    ) -> tuple[str, int]:
        """Run a headless (non-interactive) agent invocation.

        Args:
            prompt: The system prompt / agent role definition.
            task: The task to execute.
            cwd: Working directory for the subprocess.
            timeout: Maximum execution time in seconds.
            model: Optional model override.
            dangerously_skip_permissions: If True, skip permission prompts.
            role: Agent role name (used for logging and output prefixing).
            session_name: Optional session name for identification in /resume.

        Returns:
            (stdout, return_code) tuple.
        """
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
        """Run an interactive CLI session as a subprocess (returns on exit).

        Unlike interactive_exec, this uses subprocess.run so the caller regains
        control after the session finishes — enabling cleanup in finally blocks.

        Args:
            prompt: The system prompt to append.
            task: The initial user message.
            cwd: Working directory for the subprocess.
            model: Optional model override.
            role: Agent role name (used for logging and output prefixing).
            dangerously_skip_permissions: If True, skip permission prompts (--yolo for bob).
            session_name: Optional session name for identification in /resume.

        Returns:
            The subprocess exit code.
        """
        ...
