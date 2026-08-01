"""Shared pytest fixtures for the statefulness eval benchmark."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

import pytest
import structlog

sys.path.insert(0, str(Path(__file__).parent))
from parse_tools import TraceMetrics, parse_stream_json  # noqa: E402

log = structlog.get_logger()

RESULTS_BASE = Path(".factory/experiments/statefulness")


@pytest.fixture
def statefulness_results_dir() -> Path:
    """Return the statefulness results directory, creating it if needed."""
    RESULTS_BASE.mkdir(parents=True, exist_ok=True)
    return RESULTS_BASE


async def run_ceo_subprocess(
    project_path: str | Path,
    focus: str,
    timeout_s: int = 120,
    condition: str = "control",
) -> tuple[int, str, float]:
    """Run a factory CEO subprocess with stream-JSON output.

    Uses start_new_session=True for safe process group cleanup.

    Args:
        project_path: Path to the target project.
        focus: The --focus argument for the CEO.
        timeout_s: Wall-clock timeout in seconds.
        condition: "control" or "treatment" — for logging only.

    Returns:
        Tuple of (exit_code, stdout_text, duration_seconds).
    """
    cmd = [
        "factory",
        "ceo",
        str(project_path),
        "--mode",
        "improve",
        "--focus",
        focus,
        "--output-format",
        "stream-json",
        "--verbose",
        "--headless",
    ]

    log.info(
        "ceo_subprocess_start",
        project=str(project_path),
        focus=focus,
        condition=condition,
        timeout_s=timeout_s,
    )

    start_time = asyncio.get_event_loop().time()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_s,
        )
        exit_code = proc.returncode or 0
    except asyncio.TimeoutError:
        log.warning("ceo_subprocess_timeout", pid=proc.pid, timeout_s=timeout_s)
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
        except ProcessLookupError:
            pass
        stdout_bytes = b""
        if proc.stdout:
            try:
                stdout_bytes = await asyncio.wait_for(proc.stdout.read(), timeout=1)
            except (asyncio.TimeoutError, Exception):
                pass
        exit_code = 142

    duration_s = asyncio.get_event_loop().time() - start_time
    stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""

    log.info(
        "ceo_subprocess_done",
        exit_code=exit_code,
        duration_s=round(duration_s, 1),
        stdout_lines=stdout_text.count("\n"),
    )

    return exit_code, stdout_text, duration_s


def parse_trace(stdout: str) -> TraceMetrics:
    """Parse stream-JSON stdout into TraceMetrics."""
    return parse_stream_json(stdout)


def save_iteration_result(
    results_dir: Path,
    project: str,
    condition: str,
    iteration: int,
    metrics: TraceMetrics,
    exit_code: int,
    duration_s: float,
) -> Path:
    """Save per-iteration metrics to a JSON file.

    Args:
        results_dir: Base results directory.
        project: Project name.
        condition: "control" or "treatment".
        iteration: Iteration number (1-based).
        metrics: Parsed trace metrics.
        exit_code: Process exit code.
        duration_s: Wall-clock duration.

    Returns:
        Path to the written JSON file.
    """
    out_dir = results_dir / project / condition
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"iter-{iteration}.json"

    result: dict[str, Any] = {
        "project": project,
        "condition": condition,
        "iteration": iteration,
        "exit_code": exit_code,
        "duration_s": round(duration_s, 2),
        "metrics": {
            "factory_read_count": metrics.factory_read_count,
            "factory_files_read": metrics.factory_files_read,
            "agent_reinvocations": metrics.agent_reinvocations,
            "time_to_first_meaningful_action_s": metrics.time_to_first_meaningful_action_s,
            "total_tool_calls": metrics.total_tool_calls,
        },
    }

    out_path.write_text(json.dumps(result, indent=2) + "\n")
    log.info("iteration_result_saved", path=str(out_path))
    return out_path
