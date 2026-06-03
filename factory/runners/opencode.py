"""OpenCodeRunner — OpenCode CLI backend implementation."""

from __future__ import annotations

from factory.runners.abstraction import (
    AgentRunner,
    Capability,
    Request,
    Response,
    RunnerIdentity,
)

_IDENTITY = RunnerIdentity(
    name="opencode",
    display_name="OpenCode",
    binary="opencode",
    capabilities={Capability.MODEL_OVERRIDE},
)


class OpenCodeRunner(AgentRunner):
    """Runner implementation for OpenCode CLI."""

    name: str = "opencode"

    @property
    def identity(self) -> RunnerIdentity:
        return _IDENTITY

    def _build_command(
        self, request: Request, *, prompt_file: str | None = None
    ) -> list[str]:
        cmd = ["opencode", "run", request.prompt, "--format", "json"]
        if request.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if request.model:
            model = request.model
            if "/" not in model:
                model = f"anthropic/{model}"
            cmd.extend(["--model", model])
        return cmd

    def _parse_response(
        self, stdout: str, stderr: str, exit_code: int
    ) -> Response:
        return Response(output=stdout, exit_code=exit_code)
