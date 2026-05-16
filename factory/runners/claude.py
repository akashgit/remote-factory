"""ClaudeRunner — Claude Code CLI backend implementation."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import NoReturn

from factory.runners._stream import should_stream, stream_subprocess

logger = logging.getLogger(__name__)


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
    ) -> tuple[str, int]:
        """Run a headless Claude Code invocation.

        Args:
            prompt: The system prompt / agent role definition.
            task: The task to execute.
            cwd: Working directory for the subprocess.
            timeout: Maximum execution time in seconds.
            model: Optional model override.
            dangerously_skip_permissions: If True, skip permission prompts.
            role: Agent role (used for streaming prefix).

        Returns (stdout, return_code).
        """
        cmd = ["claude", "--append-system-prompt", prompt, "-p", task]
        if dangerously_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if model:
            cmd.extend(["--model", model])

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
            return f"Agent timed out after {timeout}s", 1
        except FileNotFoundError:
            logger.error("'claude' CLI not found on PATH")
            return "Error: 'claude' CLI not found on PATH", 1

        stdout = stdout_bytes.decode()
        stderr = stderr_bytes.decode()

        if proc.returncode != 0:
            logger.warning("ClaudeRunner exited with code %d: %s", proc.returncode, stderr[:200])

        return stdout, proc.returncode or 0

    def interactive_exec(
        self,
        prompt: str,
        task: str,
        cwd: Path,
        *,
        model: str | None = None,
        role: str = "ceo",
        dangerously_skip_permissions: bool = False,
    ) -> NoReturn:
        """Replace process with interactive Claude Code session.

        Args:
            prompt: The system prompt to append.
            task: The initial user message.
            cwd: Working directory (os.chdir is called before exec).
            model: Optional model override.
            role: Agent role (unused by claude, but kept for API compatibility).
            dangerously_skip_permissions: If True, skip permission prompts.
        """
        _ = role  # unused by claude runner
        cmd = [
            "claude",
            "--append-system-prompt", prompt,
        ]
        if dangerously_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        cmd.append(task)
        if model:
            cmd.extend(["--model", model])
            os.environ["FACTORY_MODEL"] = model

        logger.info("ClaudeRunner interactive_exec: cwd=%s", cwd)

        os.chdir(cwd)
        os.execvp("claude", cmd)
