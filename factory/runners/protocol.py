"""Runner protocol — interface for CLI backend implementations (v2)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from factory.runners.types import RunnerInfo, RunnerRequest, RunnerResponse


@runtime_checkable
class Runner(Protocol):
    """Protocol for CLI backend implementations.

    v2 protocol with structured request/response, capability negotiation,
    and health checks. Replaces the v1 tuple-return protocol.
    """

    @property
    def info(self) -> RunnerInfo: ...

    async def check_health(self) -> tuple[bool, str]:
        """Check if the runner is installed and authenticated.

        Returns:
            (healthy, message) — message describes the status or error.
        """
        ...

    async def headless(self, request: RunnerRequest) -> RunnerResponse:
        """Run a headless (non-interactive) agent invocation.

        Args:
            request: Fully assembled runner request.

        Returns:
            Structured response with output, exit code, optional usage and trace.
        """
        ...

    def interactive(self, request: RunnerRequest) -> RunnerResponse:
        """Run an interactive CLI session.

        Args:
            request: Runner request (prompt used as system prompt, skip_permissions
                     typically False for interactive mode).

        Returns:
            Structured response (trace is typically None for interactive sessions).
        """
        ...
