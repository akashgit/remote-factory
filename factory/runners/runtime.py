"""Runtime protocol — execution backends for agent commands."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Protocol

from factory.runners._stream import should_stream, stream_subprocess

logger = logging.getLogger(__name__)


class Runtime(Protocol):
    """Protocol for command execution backends."""

    async def execute(
        self,
        cmd: list[str],
        env: dict[str, str],
        cwd: Path,
        *,
        timeout: float = 600.0,
        stream_prefix: str | None = None,
        sanitize: bool = False,
    ) -> tuple[str, int]:
        """Execute a command headlessly, returning (stdout, return_code)."""
        ...

    def execute_interactive(
        self,
        cmd: list[str],
        env: dict[str, str],
        cwd: Path,
        *,
        requires_tty: bool = True,
    ) -> int:
        """Execute a command interactively, returning the exit code."""
        ...


class ProcessRuntime:
    """Runtime that wraps asyncio subprocess + stream_subprocess."""

    async def execute(
        self,
        cmd: list[str],
        env: dict[str, str],
        cwd: Path,
        *,
        timeout: float = 600.0,
        stream_prefix: str | None = None,
        sanitize: bool = False,
    ) -> tuple[str, int]:
        """Execute a command as an async subprocess with streaming."""
        stream = should_stream()
        prefix = stream_prefix if stream else None

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                stream_subprocess(proc, stream=stream, prefix=prefix, sanitize=sanitize),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()  # type: ignore[union-attr]
            await proc.wait()  # type: ignore[union-attr]
            logger.error("Process timed out after %ss", timeout)
            return f"Agent timed out after {timeout}s", 1
        except FileNotFoundError:
            logger.error("Command not found: %s", cmd[0])
            return f"Error: '{cmd[0]}' CLI not found on PATH", 1

        stdout = stdout_bytes.decode()
        stderr = stderr_bytes.decode()
        return_code = proc.returncode or 0

        if return_code != 0:
            logger.warning("Process exited with code %d: %s", return_code, stderr[:200])

        return stdout, return_code

    def execute_interactive(
        self,
        cmd: list[str],
        env: dict[str, str],
        cwd: Path,
        *,
        requires_tty: bool = True,
    ) -> int:
        """Execute a command interactively via subprocess.run.

        Args:
            requires_tty: If False, close stdin so CLIs that check for a
                terminal (e.g. codex) don't hang waiting for input.
        """
        stdin = None if requires_tty else subprocess.DEVNULL
        result = subprocess.run(cmd, cwd=cwd, env=env, stdin=stdin)
        return result.returncode


class TmuxRuntime:
    """Runtime that wraps _tmux_persist for interactive tmux execution."""

    async def execute(
        self,
        cmd: list[str],
        env: dict[str, str],
        cwd: Path,
        *,
        timeout: float = 600.0,
        stream_prefix: str | None = None,
        sanitize: bool = False,
    ) -> tuple[str, int]:
        """Execute via tmux, falling back to ProcessRuntime if unavailable."""
        from factory.runners._tmux_persist import tmux_available

        if not tmux_available():
            logger.warning("tmux not available; falling back to ProcessRuntime")
            process_rt = ProcessRuntime()
            return await process_rt.execute(
                cmd, env, cwd,
                timeout=timeout,
                stream_prefix=stream_prefix,
                sanitize=sanitize,
            )

        # Tmux execution delegates to the existing _tmux_persist module
        # which handles session/window management internally.
        # The cmd is expected to already be fully formed.
        from factory.runners._tmux_persist import run_in_tmux, find_project_path

        # Extract role from stream_prefix if available
        role = "unknown"
        if stream_prefix and ":" in stream_prefix:
            # prefix format: "[runner:role]"
            role = stream_prefix.split(":")[-1].rstrip("]")

        project_path = find_project_path(cwd)

        # Extract prompt file, task, and flags from the command
        # TmuxRuntime delegates to run_in_tmux which builds its own command
        prompt = ""
        task = ""
        model = None
        skip_permissions = True

        # Parse the cmd to extract prompt file content and task
        i = 0
        while i < len(cmd):
            if cmd[i] == "--append-system-prompt-file" and i + 1 < len(cmd):
                try:
                    prompt = Path(cmd[i + 1]).read_text()
                except OSError:
                    pass
                i += 2
            elif cmd[i] == "-p" and i + 1 < len(cmd):
                task = cmd[i + 1]
                i += 2
            elif cmd[i] == "--model" and i + 1 < len(cmd):
                model = cmd[i + 1]
                i += 2
            elif cmd[i] == "--dangerously-skip-permissions":
                skip_permissions = True
                i += 1
            else:
                # For non-flag args that aren't the binary name, treat as task
                if i > 0 and not cmd[i].startswith("-"):
                    task = task or cmd[i]
                i += 1

        stdout, return_code, _ = await run_in_tmux(
            prompt, task, cwd, role, project_path,
            model=model,
            dangerously_skip_permissions=skip_permissions,
        )
        return stdout, return_code

    def execute_interactive(
        self,
        cmd: list[str],
        env: dict[str, str],
        cwd: Path,
        *,
        requires_tty: bool = True,
    ) -> int:
        """Interactive execution is not supported in TmuxRuntime; use ProcessRuntime."""
        logger.warning("TmuxRuntime does not support execute_interactive; using subprocess")
        stdin = None if requires_tty else subprocess.DEVNULL
        result = subprocess.run(cmd, cwd=cwd, env=env, stdin=stdin)
        return result.returncode
