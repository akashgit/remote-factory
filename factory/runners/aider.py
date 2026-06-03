"""AiderRunner — Aider CLI backend implementation."""

from __future__ import annotations

from factory.runners.abstraction import (
    AgentRunner,
    Request,
    Response,
    RunnerIdentity,
)

_IDENTITY = RunnerIdentity(
    name="aider",
    display_name="Aider",
    binary="aider",
    capabilities=set(),
)


class AiderRunner(AgentRunner):
    """Runner implementation for Aider CLI."""

    name: str = "aider"

    @property
    def identity(self) -> RunnerIdentity:
        return _IDENTITY

    def _build_command(
        self, request: Request, *, prompt_file: str | None = None
    ) -> list[str]:
        return ["aider", "--message", request.prompt, "--yes"]

    def _parse_response(
        self, stdout: str, stderr: str, exit_code: int
    ) -> Response:
        return Response(output=stdout, exit_code=exit_code)
