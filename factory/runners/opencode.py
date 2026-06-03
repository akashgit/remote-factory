"""OpenCodeRunner — OpenCode CLI backend implementation."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from factory.runners.abstraction import (
    AgentRunner,
    Capability,
    Request,
    Response,
    RunnerIdentity,
)

logger = logging.getLogger(__name__)

_OPENCODE_IDENTITY = RunnerIdentity(
    name="opencode",
    cli_command="opencode",
    capabilities=frozenset({
        Capability.MODEL_OVERRIDE,
        Capability.SESSION_RESUME,
        Capability.INTERACTIVE,
        Capability.EFFORT_CONTROL,
        Capability.STRUCTURED_OUTPUT,
    }),
)

# Map factory effort levels to opencode --variant values
_EFFORT_TO_VARIANT: dict[str, str] = {
    "low": "minimal",
    "medium": "default",
    "high": "high",
    "xhigh": "max",
    "max": "max",
}


class OpenCodeRunner(AgentRunner):
    """Runner implementation for OpenCode CLI."""

    name: str = "opencode"

    @property
    def identity(self) -> RunnerIdentity:
        return _OPENCODE_IDENTITY

    def _build_command(self, request: Request) -> list[str]:
        """Build the opencode CLI command."""
        cmd = ["opencode"]

        # Permission handling
        if request.permission_mode == "bypassPermissions" or request.skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        if request.model:
            cmd.extend(["--model", request.model])

        # Effort maps to --variant
        if request.effort:
            variant = _EFFORT_TO_VARIANT.get(request.effort)
            if variant and variant != "default":
                cmd.extend(["--variant", variant])

        # Output format
        if request.output_format:
            fmt = "json" if request.output_format in ("json", "stream-json") else "default"
            cmd.extend(["--format", fmt])
        else:
            cmd.extend(["--format", "json"])

        if request.session_name:
            cmd.extend(["--session", request.session_name])

        return cmd

    def _build_env(self) -> dict[str, str]:
        return {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}

    def _parse_response(
        self,
        stdout: str,
        stderr: str,
        return_code: int,
    ) -> Response:
        """Parse opencode output. Attempt JSON parsing for usage data."""
        usage = None
        result_text = stdout
        try:
            data = json.loads(stdout)
            if isinstance(data, dict):
                result_text = data.get("result", stdout)
                if not isinstance(result_text, str):
                    result_text = stdout
                # Attempt to extract usage if present
                usage_block = data.get("usage")
                if usage_block and isinstance(usage_block, dict):
                    from factory.models import AgentUsage

                    usage = AgentUsage(
                        input_tokens=usage_block.get("input_tokens", 0),
                        output_tokens=usage_block.get("output_tokens", 0),
                        cache_read_tokens=usage_block.get("cache_read_tokens", 0),
                        cache_creation_tokens=usage_block.get("cache_creation_tokens", 0),
                        total_cost_usd=data.get("cost_usd", 0.0) or 0.0,
                        duration_ms=data.get("duration_ms", 0.0) or 0.0,
                        num_turns=data.get("num_turns", 0) or 0,
                        model=data.get("model", ""),
                    )
        except (json.JSONDecodeError, ValueError):
            logger.debug("Could not parse JSON output from opencode, returning raw stdout")

        return Response(stdout=result_text, return_code=return_code, usage=usage)

    def _warn_unsupported(self, request: Request) -> None:
        """Log warnings for features not natively supported."""
        if request.max_budget_usd is not None:
            logger.warning(
                "OpenCodeRunner: max_budget_usd=%.2f accepted but not natively enforced",
                request.max_budget_usd,
            )
        if request.mcp_config:
            logger.warning("OpenCodeRunner: mcp_config is not supported by opencode, ignoring")

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
    ) -> tuple[str, int, None]:
        """Run a headless OpenCode invocation (backward-compat shim)."""
        _ = session_name
        if tmux_persist:
            logger.warning("tmux_persist not supported with opencode runner")

        # Build prompt with injected features
        full = prompt
        full = self._inject_tool_restrictions(full, Request(
            prompt=prompt, task=task, cwd=cwd,
        ))
        full = f"{full}\n\n---\n\n## Current Task\n\n{task}"

        request = Request(
            prompt=full,
            task=task,
            cwd=cwd,
            timeout=timeout,
            model=model,
            skip_permissions=dangerously_skip_permissions,
            role=role,
            session_name=session_name,
        )
        response = await self.run(request)
        return response.stdout, response.return_code, None

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
        """Run an interactive OpenCode session as a subprocess."""
        import subprocess as _subprocess

        _ = role

        cmd = ["opencode"]
        if dangerously_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if model:
            cmd.extend(["--model", model])
        if session_name:
            cmd.extend(["--session", session_name])

        full_prompt = f"{prompt}\n\n---\n\n## Current Task\n\n{task}"
        cmd.append(full_prompt)

        logger.info("OpenCodeRunner interactive_run: cwd=%s", cwd)

        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        result = _subprocess.run(cmd, cwd=cwd, env=env)
        return result.returncode
