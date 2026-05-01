"""Run phase orchestration — executes research commands as subprocesses."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import structlog

from factory.models import ResearchTarget
from factory.research.models import RunResult, RunStatus
from factory.research.parser import parse_result, ResultParseError
from factory.research.store import create_run_dir, save_run_summary

log = structlog.get_logger()


async def execute_run(
    project_path: Path, config: ResearchTarget, cycle_id: str
) -> RunResult:
    """Execute the run_command from config and return a RunResult.

    Steps:
    1. Create run artifact directory
    2. Run the command via asyncio subprocess with timeout
    3. Parse the result file for the target metric
    4. Save artifacts (stdout, stderr, summary)
    """
    run_dir = create_run_dir(project_path, cycle_id)
    log.info(
        "research_run_started",
        cycle_id=cycle_id,
        command=config.run_command,
        timeout=config.timeout,
    )

    start = time.monotonic()

    try:
        proc = await asyncio.create_subprocess_shell(
            config.run_command,
            cwd=project_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=config.timeout
        )
    except asyncio.TimeoutError:
        duration = time.monotonic() - start
        log.warning("research_run_timeout", cycle_id=cycle_id, duration=duration)
        proc.kill()
        await proc.wait()
        result = RunResult(
            status=RunStatus.TIMEOUT,
            metric_value=0.0,
            duration_seconds=duration,
            artifacts_path=run_dir,
            stdout="",
            stderr="",
        )
        _save_artifacts(run_dir, result, config)
        return result

    duration = time.monotonic() - start
    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")

    if proc.returncode != 0:
        log.warning(
            "research_run_failed",
            cycle_id=cycle_id,
            returncode=proc.returncode,
            duration=duration,
        )
        result = RunResult(
            status=RunStatus.FAIL,
            metric_value=0.0,
            duration_seconds=duration,
            artifacts_path=run_dir,
            stdout=stdout,
            stderr=stderr,
        )
        _save_artifacts(run_dir, result, config)
        return result

    result_path = project_path / config.result_path
    try:
        metric_value = parse_result(result_path, config.result_parser, config.metric)
    except ResultParseError as exc:
        log.error("research_run_parse_error", cycle_id=cycle_id, error=str(exc))
        result = RunResult(
            status=RunStatus.ERROR,
            metric_value=0.0,
            duration_seconds=duration,
            artifacts_path=run_dir,
            stdout=stdout,
            stderr=stderr,
        )
        _save_artifacts(run_dir, result, config)
        return result

    log.info(
        "research_run_completed",
        cycle_id=cycle_id,
        metric=metric_value,
        duration=duration,
    )

    result = RunResult(
        status=RunStatus.PASS,
        metric_value=metric_value,
        duration_seconds=duration,
        artifacts_path=run_dir,
        stdout=stdout,
        stderr=stderr,
    )
    _save_artifacts(run_dir, result, config)
    return result


def _save_artifacts(run_dir: Path, result: RunResult, config: ResearchTarget) -> None:
    """Persist stdout, stderr, and summary to the run directory."""
    (run_dir / "stdout.log").write_text(result.stdout)
    (run_dir / "stderr.log").write_text(result.stderr)
    save_run_summary(run_dir, {
        "status": result.status.value,
        "metric": config.metric,
        "metric_value": result.metric_value,
        "duration_seconds": result.duration_seconds,
        "command": config.run_command,
    })
