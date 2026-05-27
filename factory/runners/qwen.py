"""QwenRunner — Qwen Code CLI backend implementation."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

from factory.runners._stream import should_stream, stream_subprocess

logger = logging.getLogger(__name__)

_auth_warned = False


def _warn_auth() -> None:
    """Log a warning if neither DASHSCOPE_API_KEY nor QWEN_API_KEY is set (once per process)."""
    global _auth_warned  # noqa: PLW0603
    if _auth_warned:
        return
    if os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY"):
        _auth_warned = True
        return
    _auth_warned = True
    logger.warning(
        "Neither DASHSCOPE_API_KEY nor QWEN_API_KEY is set. "
        "Qwen Code may fail to authenticate. "
        "Set one directly or add it to a config.toml credential profile: "
        '[credentials.qwen] DASHSCOPE_API_KEY = "sk-..."'
    )


def _make_qwen_env() -> dict[str, str]:
    """Build subprocess env: strip VIRTUAL_ENV."""
    return {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}


def is_qwen_dry_run() -> bool:
    """Return True if Qwen dry-run mode is enabled."""
    from factory.user_config import resolve

    val = resolve("qwen_dry_run", env_var="FACTORY_QWEN_DRY_RUN") or ""
    return val.lower() in ("1", "true", "yes")


class QwenRunner:
    """Runner implementation for Qwen Code CLI."""

    name: str = "qwen"

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
        """Run a headless Qwen Code invocation.

        Returns (stdout, return_code).
        """
        _ = session_name
        if is_qwen_dry_run():
            return self._dry_run_response(role, cwd, task)

        _warn_auth()

        cmd = [
            "qwen",
            "--append-system-prompt", prompt,
            "-p", task,
            "--yolo",
            "--output-format", "text",
        ]
        if model:
            cmd.extend(["--model", model])

        logger.info("QwenRunner headless: cwd=%s, model=%s, role=%s", cwd, model, role)

        env = _make_qwen_env()

        stream = should_stream()
        prefix = f"[qwen:{role}]" if stream else None

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
            logger.error("QwenRunner timed out after %ss", timeout)
            return f"Agent timed out after {timeout}s", 1
        except FileNotFoundError:
            logger.error("'qwen' CLI not found on PATH")
            return "Error: 'qwen' CLI not found on PATH", 1

        stdout = stdout_bytes.decode()
        stderr = stderr_bytes.decode()

        if proc.returncode != 0:
            logger.warning("QwenRunner exited with code %d: %s", proc.returncode, stderr[:200])

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
        """Run an interactive Qwen Code session as a subprocess.

        Returns the exit code so the caller can clean up in a finally block.
        """
        _ = role, session_name

        if is_qwen_dry_run():
            print("[DRY-RUN] Would exec: qwen (interactive)")
            print(f"[DRY-RUN] Task: {task[:200]}...")
            return 0

        _warn_auth()

        cmd = ["qwen", "--append-system-prompt", prompt]
        if dangerously_skip_permissions:
            cmd.append("--yolo")
        cmd.append(task)
        if model:
            cmd.extend(["--model", model])

        logger.info("QwenRunner interactive_run: cwd=%s", cwd)

        env = _make_qwen_env()
        result = subprocess.run(cmd, cwd=cwd, env=env)
        return result.returncode

    def _dry_run_response(self, role: str, cwd: Path, task: str) -> tuple[str, int]:
        """Return a stub response for dry-run mode."""
        response = (
            f"[DRY-RUN] QwenRunner would have executed:\n"
            f"  role: {role}\n"
            f"  cwd: {cwd}\n"
            f"  task: {task[:100]}...\n"
            f"\n"
            f"Dry-run stub response: Task acknowledged."
        )
        logger.info("QwenRunner dry-run: role=%s, cwd=%s", role, cwd)
        return response, 0
