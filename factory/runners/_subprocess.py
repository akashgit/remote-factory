"""Shared subprocess executor for all runners."""

from __future__ import annotations

import asyncio
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
) -> AgentRunResult:
    """Run a subprocess with streaming, timeout, and error handling.

    This is the shared execution path for all runners, eliminating
    ~30 lines of duplicated subprocess code per runner.
    """
    stream = should_stream()
    prefix = f"[{runner_name}:{role}]" if stream else None

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
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
        log.error(f"{runner_name}_timed_out", timeout=timeout)
        return AgentRunResult(
            stdout=f"Agent timed out after {timeout}s",
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
        log.warning(f"{runner_name}_nonzero_exit", code=return_code, stderr=stderr[:200])

    return AgentRunResult(
        stdout=stdout,
        return_code=return_code,
        metadata={"stderr": stderr},
    )
