"""ClaudeRunner — Claude Code CLI backend implementation."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from factory.runners.abstraction import (
    AgentRunner,
    Capability,
    Request,
    Response,
    RunnerIdentity,
)

if TYPE_CHECKING:
    from factory.models import AgentUsage

logger = logging.getLogger(__name__)

_IDENTITY = RunnerIdentity(
    name="claude",
    display_name="Claude Code",
    binary="claude",
    capabilities={
        Capability.MODEL_OVERRIDE,
        Capability.SESSION_RESUME,
        Capability.SYSTEM_PROMPT_FILE,
        Capability.STRUCTURED_OUTPUT,
        Capability.STREAMING,
        Capability.INTERACTIVE,
    },
)


def _parse_usage(data: dict) -> AgentUsage:
    """Extract AgentUsage from Claude Code JSON output."""
    from factory.models import AgentUsage

    usage_block = data.get("usage", {})
    return AgentUsage(
        input_tokens=usage_block.get("input_tokens", 0),
        output_tokens=usage_block.get("output_tokens", 0),
        cache_read_tokens=usage_block.get("cache_read_input_tokens", 0),
        cache_creation_tokens=usage_block.get("cache_creation_input_tokens", 0),
        total_cost_usd=data.get("cost_usd", 0.0) or 0.0,
        duration_ms=data.get("duration_ms", 0.0) or 0.0,
        num_turns=data.get("num_turns", 0) or 0,
        model=data.get("model", ""),
    )


class ClaudeRunner(AgentRunner):
    """Runner implementation for Claude Code CLI."""

    name: str = "claude"

    @property
    def identity(self) -> RunnerIdentity:
        return _IDENTITY

    def _build_command(
        self, request: Request, *, prompt_file: str | None = None
    ) -> list[str]:
        cmd = ["claude"]
        if prompt_file:
            cmd.extend(["--append-system-prompt-file", prompt_file])
        cmd.extend(["-p", request.task])
        cmd.extend(["--output-format", "json"])
        if request.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if request.model:
            cmd.extend(["--model", request.model])
        if request.session_name:
            cmd.extend(["--name", request.session_name])
        return cmd

    def _parse_response(
        self, stdout: str, stderr: str, exit_code: int
    ) -> Response:
        usage = None
        result_text = stdout
        try:
            data = json.loads(stdout)
            if isinstance(data, dict):
                result_value = data.get("result", stdout)
                result_text = result_value if isinstance(result_value, str) else stdout
                usage = _parse_usage(data)
        except (json.JSONDecodeError, ValueError):
            logger.debug("Could not parse JSON output, returning raw stdout")

        return Response(output=result_text, exit_code=exit_code, usage=usage)

    def _build_env(self, request: Request) -> dict[str, str]:
        env = super()._build_env(request)
        if request.model:
            env["FACTORY_MODEL"] = request.model
        return env

    def run_interactive(self, request: Request) -> int:
        """Override to use Claude's interactive command format (no -p, no --output-format)."""
        import subprocess
        import tempfile

        cwd = Path(request.cwd)
        prompt_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="factory-prompt-", delete=False,
        )
        try:
            prompt_file.write(request.system_prompt)
            prompt_file.close()

            cmd = [
                "claude",
                "--append-system-prompt-file", prompt_file.name,
            ]
            if request.skip_permissions:
                cmd.append("--dangerously-skip-permissions")
            cmd.append(request.task)
            if request.model:
                cmd.extend(["--model", request.model])
            if request.session_name:
                cmd.extend(["--name", request.session_name])

            logger.info("ClaudeRunner interactive: cwd=%s", cwd)

            result = subprocess.run(cmd, cwd=cwd)
            return result.returncode
        finally:
            Path(prompt_file.name).unlink(missing_ok=True)

    async def run(self, request: Request) -> Response:
        """Override to handle tmux_persist before delegating to base."""
        if request.tmux_persist:
            from factory.runners._tmux_persist import (
                find_project_path,
                run_in_tmux,
                tmux_available,
            )

            if tmux_available():
                cwd = Path(request.cwd)
                stdout, return_code, usage = await run_in_tmux(
                    request.system_prompt,
                    request.task,
                    cwd,
                    request.role,
                    find_project_path(cwd),
                    model=request.model,
                    dangerously_skip_permissions=request.skip_permissions,
                )
                return Response(output=stdout, exit_code=return_code, usage=usage)
            logger.warning("tmux not available; falling back to headless")

        return await super().run(request)
