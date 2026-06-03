"""Runner protocol — interface for CLI backend implementations.

Defines both the new Request/Response interface (run, run_interactive)
and the legacy interface (headless, interactive_run) for backward compat
during the Phase 2-3 migration.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from factory.models import AgentUsage
    from factory.runners.abstraction import Request, Response


class Runner(Protocol):
    """Protocol for CLI backend implementations (claude, bob, etc.)."""

    name: str

    # ── New interface (Phase 2+) ──────────────────────────────────

    async def run(self, request: Request) -> Response:
        """Run a headless agent invocation via Request/Response types."""
        ...

    def run_interactive(self, request: Request) -> int:
        """Run an interactive session with inherited stdio. Returns exit code."""
        ...

    # ── Legacy interface (backward compat, removed in Phase 4) ───

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
    ) -> tuple[str, int, AgentUsage | None]:
        """Run a headless invocation (legacy — use run() instead)."""
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
        """Run an interactive session (legacy — use run_interactive() instead)."""
        ...
