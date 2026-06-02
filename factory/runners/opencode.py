"""OpenCodeRunner — OpenCode CLI backend implementation."""

from __future__ import annotations

import logging

from factory.runners.cli_adapter import CLIAdapter
from factory.runners.types import (
    RunnerCapability,
    RunnerRequest,
    RunnerResponse,
)

logger = logging.getLogger(__name__)


class OpenCodeRunner(CLIAdapter):
    """Runner implementation for OpenCode CLI."""

    name: str = "opencode"

    def __init__(self) -> None:
        super().__init__(
            name="opencode",
            display_name="OpenCode",
            capabilities={RunnerCapability.MODEL_OVERRIDE, RunnerCapability.STRUCTURED_OUTPUT},
            binary="opencode",
        )

    def _build_command(
        self,
        request: RunnerRequest,
        *,
        prompt_file: str | None = None,
    ) -> list[str]:
        cmd = ["opencode", "run", "--format", "json", request.prompt]
        return cmd

    def _parse_output(
        self,
        stdout: str,
        stderr: str,
        exit_code: int,
    ) -> RunnerResponse:
        return RunnerResponse(output=stdout, exit_code=exit_code)
