"""ClaudeRunner — Claude Code CLI backend implementation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from factory.runners._stream import should_stream, stream_subprocess
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


def _parse_usage(data: dict) -> "AgentUsage":
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


_CLAUDE_IDENTITY = RunnerIdentity(
    name="claude",
    cli_command="claude",
    capabilities=frozenset({
        Capability.MODEL_OVERRIDE,
        Capability.SESSION_RESUME,
        Capability.SYSTEM_PROMPT_FILE,
        Capability.STREAMING,
        Capability.INTERACTIVE,
        Capability.STRUCTURED_OUTPUT,
        Capability.TOOL_FILTERING,
        Capability.PERMISSION_MODES,
        Capability.BUDGET_CAP,
        Capability.EFFORT_CONTROL,
        Capability.APPEND_SYSTEM_PROMPT,
        Capability.MCP_CONFIG,
        Capability.USAGE_TRACKING,
    }),
)


class ClaudeRunner(AgentRunner):
    """Runner implementation for Claude Code CLI."""

    name: str = "claude"

    @property
    def identity(self) -> RunnerIdentity:
        return _CLAUDE_IDENTITY

    def _build_command(self, request: Request) -> list[str]:
        """Build the claude CLI command with all v2 fields mapped to flags."""
        # Prompt is written to a temp file by run()/headless() — use placeholder.
        # The actual file path is set in the headless() method.
        cmd = ["claude"]

        # permission_mode takes priority over skip_permissions
        if request.permission_mode:
            cmd.extend(["--permission-mode", request.permission_mode])
        elif request.skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        if request.model:
            cmd.extend(["--model", request.model])
        if request.session_name:
            cmd.extend(["--name", request.session_name])

        # v2 fields
        if request.allowed_tools:
            cmd.append("--allowedTools")
            cmd.extend(request.allowed_tools)
        if request.disallowed_tools:
            cmd.append("--disallowedTools")
            cmd.extend(request.disallowed_tools)
        if request.max_budget_usd is not None:
            cmd.extend(["--max-budget-usd", str(request.max_budget_usd)])
        if request.effort:
            cmd.extend(["--effort", request.effort])
        if request.output_format:
            cmd.extend(["--output-format", request.output_format])
        else:
            cmd.extend(["--output-format", "json"])
        if request.append_system_prompt:
            cmd.extend(["--append-system-prompt", request.append_system_prompt])
        if request.mcp_config:
            for cfg in request.mcp_config:
                cmd.extend(["--mcp-config", cfg])

        return cmd

    def _parse_response(
        self,
        stdout: str,
        stderr: str,
        return_code: int,
    ) -> Response:
        """Parse Claude Code JSON output into a Response."""
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

        return Response(stdout=result_text, return_code=return_code, usage=usage)

    # -- Backward-compatible headless() shim --
    # Existing callers use runner.headless() directly. This preserves that interface.

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
    ) -> tuple[str, int, "AgentUsage | None"]:
        """Run a headless Claude Code invocation (backward-compat shim)."""
        if tmux_persist:
            from factory.runners._tmux_persist import find_project_path, run_in_tmux, tmux_available

            if tmux_available():
                return await run_in_tmux(
                    prompt, task, cwd, role, find_project_path(cwd),
                    model=model,
                    dangerously_skip_permissions=dangerously_skip_permissions,
                )
            logger.warning("tmux not available; falling back to headless")

        prompt_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="factory-prompt-", delete=False,
        )
        try:
            prompt_file.write(prompt)
            prompt_file.close()

            cmd = [
                "claude", "--append-system-prompt-file", prompt_file.name,
                "-p", task,
                "--output-format", "json",
            ]
            if dangerously_skip_permissions:
                cmd.append("--dangerously-skip-permissions")
            if model:
                cmd.extend(["--model", model])
            if session_name:
                cmd.extend(["--name", session_name])

            logger.info("ClaudeRunner headless: cwd=%s, model=%s", cwd, model)

            env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
            if model:
                env["FACTORY_MODEL"] = model

            stream = should_stream()
            prefix = f"[claude:{role}]" if stream else None

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=env,
                )
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    stream_subprocess(proc, stream=stream, prefix=prefix),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()  # type: ignore[union-attr]
                await proc.wait()  # type: ignore[union-attr]
                logger.error("ClaudeRunner timed out after %ss", timeout)
                return f"Agent timed out after {timeout}s", 1, None
            except FileNotFoundError:
                logger.error("'claude' CLI not found on PATH")
                return "Error: 'claude' CLI not found on PATH", 1, None

            raw_stdout = stdout_bytes.decode()
            stderr = stderr_bytes.decode()
            return_code = proc.returncode or 0

            if return_code != 0:
                logger.warning("ClaudeRunner exited with code %d: %s", return_code, stderr[:200])

            usage = None
            result_text = raw_stdout
            try:
                data = json.loads(raw_stdout)
                if isinstance(data, dict):
                    result_value = data.get("result", raw_stdout)
                    result_text = result_value if isinstance(result_value, str) else raw_stdout
                    usage = _parse_usage(data)
            except (json.JSONDecodeError, ValueError):
                logger.debug("Could not parse JSON output, returning raw stdout")

            return result_text, return_code, usage
        finally:
            Path(prompt_file.name).unlink(missing_ok=True)

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
        """Run an interactive Claude Code session as a subprocess."""
        _ = role
        prompt_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="factory-prompt-", delete=False,
        )
        try:
            prompt_file.write(prompt)
            prompt_file.close()

            cmd = [
                "claude",
                "--append-system-prompt-file", prompt_file.name,
            ]
            if dangerously_skip_permissions:
                cmd.append("--dangerously-skip-permissions")
            cmd.append(task)
            if model:
                cmd.extend(["--model", model])
                os.environ["FACTORY_MODEL"] = model
            if session_name:
                cmd.extend(["--name", session_name])

            logger.info("ClaudeRunner interactive_run: cwd=%s", cwd)

            result = subprocess.run(cmd, cwd=cwd)
            return result.returncode
        finally:
            Path(prompt_file.name).unlink(missing_ok=True)
