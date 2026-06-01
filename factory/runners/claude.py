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
        """Run a headless Claude Code invocation.

        Args:
            prompt: The system prompt / agent role definition.
            task: The task to execute.
            cwd: Working directory for the subprocess.
            timeout: Maximum execution time in seconds.
            model: Optional model override.
            dangerously_skip_permissions: If True, skip permission prompts.
            role: Agent role (used for streaming prefix).
            session_name: Optional session name for identification in /resume.
            tmux_persist: If True, run the agent interactively in a tmux window.

        Returns (stdout, return_code, usage).
        """
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
        """Run an interactive Claude Code session as a subprocess.

        Returns the exit code so the caller can clean up in a finally block.
        """
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
