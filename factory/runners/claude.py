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
from factory.runners.config import AgentLaunchConfig
from factory.runners.protocol import AgentResult

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


class ClaudeCodeAgent:
    """Agent implementation for Claude Code CLI (pure command building).

    Maps AgentLaunchConfig semantic fields to Claude Code CLI flags:

        system_prompt       → --system-prompt
        append_system_prompt → --append-system-prompt-file (via temp file)
        task                → -p <task> (headless) or positional (interactive)
        allowed_tools       → --allowedTools "Tool1 Tool2"
        disallowed_tools    → --disallowedTools "Tool1 Tool2"
        model               → --model
        permissions         → --dangerously-skip-permissions / --permission-mode
        session_name        → --name
        max_budget_usd      → --max-budget-usd
        add_dirs            → --add-dir
        mode=headless       → --output-format json
    """

    name: str = "claude"

    def __init__(self) -> None:
        self._prompt_files: list[Path] = []

    def get_launch_command(self, config: AgentLaunchConfig) -> list[str]:
        """Build the claude CLI command from semantic config fields."""
        cmd = ["claude"]

        # -- System prompt --
        if config.system_prompt:
            cmd.extend(["--system-prompt", config.system_prompt])

        if config.append_system_prompt:
            prompt_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", prefix="factory-prompt-", delete=False,
            )
            prompt_file.write(config.append_system_prompt)
            prompt_file.close()
            self._prompt_files.append(Path(prompt_file.name))
            cmd.extend(["--append-system-prompt-file", prompt_file.name])

        # -- Tool control --
        if config.allowed_tools:
            cmd.extend(["--allowedTools", " ".join(config.allowed_tools)])

        if config.disallowed_tools:
            cmd.extend(["--disallowedTools", " ".join(config.disallowed_tools)])

        # -- Permissions --
        if config.permissions == "permissionless":
            cmd.append("--dangerously-skip-permissions")

        # -- Task (headless vs interactive) --
        if config.mode == "interactive":
            cmd.append(config.task)
        else:
            cmd.extend(["-p", config.task, "--output-format", "json"])

        # -- Model --
        if config.model:
            cmd.extend(["--model", config.model])

        # -- Session name --
        if config.session_name:
            cmd.extend(["--name", config.session_name])

        # -- Budget --
        if config.max_budget_usd is not None:
            cmd.extend(["--max-budget-usd", str(config.max_budget_usd)])

        # -- Additional dirs --
        if config.add_dirs:
            for d in config.add_dirs:
                cmd.extend(["--add-dir", str(d)])

        return cmd

    def get_environment(self, config: AgentLaunchConfig) -> dict[str, str]:
        """Build subprocess environment for Claude Code."""
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        if config.model:
            env["FACTORY_MODEL"] = config.model
        return env

    def parse_output(self, stdout: str, return_code: int) -> AgentResult:
        """Parse Claude Code JSON output into AgentResult."""
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

        return AgentResult(output=result_text, return_code=return_code, usage=usage)

    def preflight(self) -> None:
        """No preflight checks needed for Claude Code."""

    def cleanup(self) -> None:
        """Clean up temporary prompt files."""
        for f in self._prompt_files:
            f.unlink(missing_ok=True)
        self._prompt_files.clear()


class ClaudeRunner:
    """Runner implementation for Claude Code CLI."""

    name: str = "claude"

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
        """Run a headless Claude Code invocation."""
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
