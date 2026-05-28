"""OpenCodeRunner — OpenCode CLI backend implementation."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

from factory.runners._stream import should_stream, stream_subprocess

logger = logging.getLogger(__name__)


def _make_opencode_env() -> dict[str, str]:
    """Build subprocess env: strip VIRTUAL_ENV."""
    return {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}


def is_opencode_dry_run() -> bool:
    """Return True if OpenCode dry-run mode is enabled."""
    from factory.user_config import resolve

    val = resolve("opencode_dry_run", env_var="FACTORY_OPENCODE_DRY_RUN") or ""
    return val.lower() in ("1", "true", "yes")


def _combine_prompt_and_task(prompt: str, task: str) -> str:
    """Prepend system prompt to task since OpenCode has no --append-system-prompt flag."""
    return f"{prompt}\n\n---\n\n{task}"


class OpenCodeRunner:
    """Runner implementation for OpenCode CLI."""

    name: str = "opencode"

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
        """Run a headless OpenCode invocation.

        Returns (stdout, return_code).
        """
        _ = session_name
        if is_opencode_dry_run():
            return self._dry_run_response(role, cwd, task)

        combined = _combine_prompt_and_task(prompt, task)

        cmd = [
            "opencode",
            "run",
            combined,
            "--dir", str(cwd),
            "--format", "default",
        ]
        if dangerously_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if model:
            cmd.extend(["--model", model])

        logger.info("OpenCodeRunner headless: cwd=%s, model=%s, role=%s", cwd, model, role)

        env = _make_opencode_env()

        stream = should_stream()
        prefix = f"[opencode:{role}]" if stream else None

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
            logger.error("OpenCodeRunner timed out after %ss", timeout)
            return f"Agent timed out after {timeout}s", 1
        except FileNotFoundError:
            logger.error("'opencode' CLI not found on PATH")
            return "Error: 'opencode' CLI not found on PATH", 1

        stdout = stdout_bytes.decode()
        stderr = stderr_bytes.decode()

        if proc.returncode != 0:
            logger.warning("OpenCodeRunner exited with code %d: %s", proc.returncode, stderr[:200])

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
        """Run an interactive OpenCode session as a subprocess.

        Returns the exit code so the caller can clean up in a finally block.
        """
        _ = role, session_name

        if is_opencode_dry_run():
            print("[DRY-RUN] Would exec: opencode run --interactive")
            print(f"[DRY-RUN] Task: {task[:200]}...")
            return 0

        combined = _combine_prompt_and_task(prompt, task)

        cmd = ["opencode", "run", "--interactive", combined, "--dir", str(cwd)]
        if dangerously_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if model:
            cmd.extend(["--model", model])

        logger.info("OpenCodeRunner interactive_run: cwd=%s", cwd)

        env = _make_opencode_env()
        result = subprocess.run(cmd, cwd=cwd, env=env)
        return result.returncode

    def _dry_run_response(self, role: str, cwd: Path, task: str) -> tuple[str, int]:
        """Return a stub response for dry-run mode."""
        response = (
            f"[DRY-RUN] OpenCodeRunner would have executed:\n"
            f"  role: {role}\n"
            f"  cwd: {cwd}\n"
            f"  task: {task[:100]}...\n"
            f"\n"
            f"Dry-run stub response: Task acknowledged."
        )
        logger.info("OpenCodeRunner dry-run: role=%s, cwd=%s", role, cwd)
        return response, 0
