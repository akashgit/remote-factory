"""ClaudeRunner — Claude Code CLI backend implementation."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

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
        session_name: str | None = None,
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
            session_name: Optional session name for identification in /resume.

        Returns (stdout, return_code).
        """
        cmd = ["claude", "--append-system-prompt", prompt, "-p", task]
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
            return f"Agent timed out after {timeout}s", 1
        except FileNotFoundError:
            logger.error("'claude' CLI not found on PATH")
            return "Error: 'claude' CLI not found on PATH", 1

        stdout = stdout_bytes.decode()
        stderr = stderr_bytes.decode()

        if proc.returncode != 0:
            logger.warning("ClaudeRunner exited with code %d: %s", proc.returncode, stderr[:200])

        return stdout, proc.returncode or 0

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
        if session_name:
            cmd.extend(["--name", session_name])

        logger.info("ClaudeRunner interactive_run: cwd=%s", cwd)

        result = subprocess.run(cmd, cwd=cwd)
        return result.returncode
