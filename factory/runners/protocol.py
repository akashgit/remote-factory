"""Runner protocol — interface for CLI backend implementations."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from factory.models import AgentRunRequest, AgentRunResult


@dataclass(frozen=True)
class RunnerMeta:
    """Metadata describing a runner's identity, binary, and capabilities."""

    name: str
    display_name: str
    binary: str
    install_hint: str
    required_env_vars: list[str] = field(default_factory=list)
    supports_model_override: bool = True
    supports_interactive: bool = True
    supports_streaming: bool = True
    supports_usage_telemetry: bool = False
    supports_session_name: bool = False
    custom_auth_check: Callable[[], bool] | None = None

    def is_available(self) -> bool:
        """Check if the runner binary is on PATH."""
        return shutil.which(self.binary) is not None

    def check_auth(self) -> bool:
        """Check if authentication is available.

        Uses ``custom_auth_check`` when provided (e.g. Bob's file-based auth),
        otherwise falls back to checking ``required_env_vars``.
        """
        if self.custom_auth_check is not None:
            return self.custom_auth_check()
        import os
        return all(os.environ.get(v) for v in self.required_env_vars)


class Runner(Protocol):
    """Protocol for CLI backend implementations (claude, bob, etc.)."""

    name: str

    @classmethod
    def metadata(cls) -> RunnerMeta:
        """Return metadata about this runner."""
        ...

    def build_command(self, request: AgentRunRequest) -> tuple[list[str], dict[str, str], list[Path]]:
        """Build the CLI command, env dict, and temp files for a headless invocation."""
        ...

    async def headless(self, request: AgentRunRequest) -> AgentRunResult:
        """Run a headless (non-interactive) agent invocation."""
        ...

    def interactive_run(self, request: AgentRunRequest) -> int:
        """Run an interactive CLI session as a subprocess (returns on exit)."""
        ...
