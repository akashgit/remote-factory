"""Shared subprocess executor for all runners."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

import structlog

from factory.models import AgentRunResult
from factory.runners._stream import should_stream, stream_subprocess

log = structlog.get_logger()


def make_dry_run_result(runner_name: str, role: str, cwd: Path, task: str) -> AgentRunResult:
    """Return a stub AgentRunResult for dry-run mode."""
    stdout = (
        f"[DRY-RUN] {runner_name} would have executed:\n"
        f"  role: {role}\n"
        f"  cwd: {cwd}\n"
        f"  task: {task[:100]}...\n"
        f"\n"
        f"Dry-run stub response: Task acknowledged."
    )
    log.info(f"{runner_name}_dry_run", role=role, cwd=str(cwd))
    return AgentRunResult(stdout=stdout, return_code=0)


async def run_subprocess(
    cmd: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: float,
    runner_name: str,
    role: str,
    sanitize: bool = False,
    max_timeout: float | None = None,
    activity_mode: str = "line",
) -> AgentRunResult:
    """Run a subprocess with streaming, timeout, and error handling.

    This is the shared execution path for all runners, eliminating
    ~30 lines of duplicated subprocess code per runner.

    Args:
        timeout: Inactivity timeout — kills the subprocess if no output is
            produced for this many seconds.
        max_timeout: Hard wall-clock backstop via ``asyncio.wait_for``.
            Catches pathological trickle-output that keeps the inactivity
            watchdog alive indefinitely. When None (default), auto-derived
            as ``max(timeout * 2, 3600.0)`` — guaranteeing the inactivity
            watchdog can always fire before the wall-clock backstop.
    """
    if max_timeout is None:
        max_timeout = max(timeout * 2, 3600.0)
    elif max_timeout <= timeout:
        log.warning("max_timeout_le_inactivity", max_timeout=max_timeout, inactivity_timeout=timeout)

    stream = should_stream()
    prefix = f"[{runner_name}:{role}]" if stream else None

    log.info(
        f"{runner_name}_subprocess_start",
        role=role,
        inactivity_timeout=timeout,
        max_timeout=max_timeout,
    )

    killed_by_watchdog: list[bool] = [False]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            start_new_session=True,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            stream_subprocess(
                proc,
                stream=stream,
                prefix=prefix,
                sanitize=sanitize,
                inactivity_timeout=timeout,
                killed_by_watchdog=killed_by_watchdog,
                activity_mode=activity_mode,
            ),
            timeout=max_timeout,
        )
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # type: ignore[union-attr]
        except (ProcessLookupError, OSError):
            proc.kill()  # type: ignore[union-attr]
        await proc.wait()  # type: ignore[union-attr]
        log.error(
            f"{runner_name}_max_timeout",
            max_timeout=max_timeout,
            inactivity_timeout=timeout,
        )
        return AgentRunResult(
            stdout=f"Agent exceeded max wall-clock timeout ({max_timeout}s)",
            return_code=1,
        )
    except FileNotFoundError:
        binary = cmd[0] if cmd else runner_name
        log.error(f"{runner_name}_not_found", binary=binary)
        return AgentRunResult(
            stdout=f"Error: '{binary}' CLI not found on PATH",
            return_code=1,
        )

    stdout = stdout_bytes.decode()
    stderr = stderr_bytes.decode()
    return_code = proc.returncode or 0

    if return_code != 0:
        if killed_by_watchdog[0]:
            log.warning(
                f"{runner_name}_inactivity_timeout",
                inactivity_timeout=timeout,
                role=role,
            )
            return AgentRunResult(
                stdout=f"Agent killed after {timeout}s of inactivity",
                return_code=1,
                metadata={"stderr": stderr},
            )
        log.warning(f"{runner_name}_nonzero_exit", code=return_code, stderr=stderr[:200])

    return AgentRunResult(
        stdout=stdout,
        return_code=return_code,
        metadata={"stderr": stderr},
    )
