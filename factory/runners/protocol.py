"""Runner protocol — interface for CLI backend implementations."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn, Protocol


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
    ) -> tuple[str, int]:
        """Run a headless (non-interactive) agent invocation.

        Args:
            prompt: The system prompt / agent role definition.
            task: The task to execute.
            cwd: Working directory for the subprocess.
            timeout: Maximum execution time in seconds.
            model: Optional model override.
            dangerously_skip_permissions: If True, skip permission prompts.
            role: Agent role name (used by bob for custom mode selection).

        Returns:
            (stdout, return_code) tuple.
        """
        ...

    def interactive_exec(
        self,
        prompt: str,
        task: str,
        cwd: Path,
        *,
        model: str | None = None,
        role: str = "ceo",
    ) -> NoReturn:
        """Replace the current process with an interactive CLI session.

        This function does not return — it calls os.execvp to replace the process.

        Args:
            prompt: The system prompt to append.
            task: The initial user message.
            cwd: Working directory (os.chdir is called before exec).
            model: Optional model override.
            role: Agent role name (used by bob for custom mode selection).
        """
        ...
