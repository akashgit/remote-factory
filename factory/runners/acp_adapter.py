"""ACPAdapter — ACP client adapter for ACP-compatible agents."""

from __future__ import annotations

import logging

from factory.runners.cli_adapter import CLIAdapter
from factory.runners.types import (
    RunnerCapability,
    RunnerRequest,
    RunnerResponse,
)

try:
    import agent_client_protocol as _acp  # type: ignore[import-untyped]  # noqa: F401
    ACP_AVAILABLE = True
except ImportError:
    ACP_AVAILABLE = False

logger = logging.getLogger(__name__)


class ACPAdapter(CLIAdapter):
    """ACP client adapter — one implementation for many ACP-compatible agents.

    For v1, this is a CLI subprocess adapter that spawns the agent's regular
    CLI (not ACP mode) using the base CLIAdapter subprocess logic. It marks
    itself as ACP-capable for future upgrade to full JSON-RPC.
    """

    def __init__(
        self,
        command: list[str],
        name: str,
        display_name: str,
        capabilities: set[RunnerCapability] | None = None,
    ) -> None:
        caps = capabilities or set()
        caps.update({RunnerCapability.ACP, RunnerCapability.EXECUTION_TRACE})
        super().__init__(
            name=name,
            display_name=display_name,
            capabilities=caps,
            binary=command[0],
        )
        self._command = command

    async def check_health(self) -> tuple[bool, str]:
        if not ACP_AVAILABLE:
            return False, "agent-client-protocol package not installed"
        return await super().check_health()

    async def headless(self, request: RunnerRequest) -> RunnerResponse:  # type: ignore[override]
        if not ACP_AVAILABLE:
            return RunnerResponse(
                output="Error: agent-client-protocol package not installed",
                exit_code=1,
            )
        # For v1, fall back to CLI adapter's subprocess approach
        # TODO: Full ACP JSON-RPC implementation in Phase 2
        return await super().headless(request)

    def _build_command(
        self,
        request: RunnerRequest,
        *,
        prompt_file: str | None = None,
    ) -> list[str]:
        cmd = list(self._command)
        if prompt_file:
            cmd.append(prompt_file)
        cmd.append(request.prompt)
        return cmd

    def _parse_output(
        self,
        stdout: str,
        stderr: str,
        exit_code: int,
    ) -> RunnerResponse:
        return RunnerResponse(output=stdout, exit_code=exit_code)
