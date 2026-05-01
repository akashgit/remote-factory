"""Research run infrastructure — execute commands, parse results, manage artifacts."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import structlog

from factory.models import ResearchTarget, ResultParseError, RunResult, RunStatus

log = structlog.get_logger()


# ── result parsing ───────────────────────────────────────────────


def parse_result(result_path: Path, result_parser: str, metric: str) -> float:
    """Parse a result file and extract the target metric as a float.

    Supports dotted paths (``results.accuracy``) and slash-ratio paths
    (``resolved/total`` computes numerator / denominator).
    """
    if result_parser != "json":
        raise ResultParseError(f"unsupported parser: {result_parser}")

    if not result_path.exists():
        raise ResultParseError(f"result file not found: {result_path}")

    try:
        data = json.loads(result_path.read_text())
    except json.JSONDecodeError as exc:
        raise ResultParseError(f"invalid JSON in {result_path}: {exc}") from exc

    log.debug("parsing_result", path=str(result_path), metric=metric)

    if "/" in metric:
        return _parse_ratio(data, metric)
    return _navigate(data, metric)


def _navigate(data: dict, key_path: str) -> float:
    """Walk a dotted key path and return the leaf as float."""
    parts = key_path.split(".")
    current: object = data
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            raise ResultParseError(f"key path '{key_path}' not found in result data")
        current = current[part]

    try:
        return float(current)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ResultParseError(
            f"value at '{key_path}' is not numeric: {current!r}"
        ) from exc


def _parse_ratio(data: dict, metric: str) -> float:
    """Parse a slash-ratio path like ``resolved/total``."""
    parts = metric.split("/")
    if len(parts) != 2:
        raise ResultParseError(f"ratio metric must have exactly two parts: {metric}")

    numerator_key, denominator_key = parts
    numerator = _navigate(data, numerator_key)
    denominator = _navigate(data, denominator_key)

    if denominator == 0:
        raise ResultParseError(f"denominator '{denominator_key}' is zero")

    return numerator / denominator


# ── directory management ─────────────────────────────────────────


def ensure_research_dir(project_path: Path) -> Path:
    """Create ``.factory/research/runs/`` if needed and return the research dir."""
    research_dir = project_path / ".factory" / "research"
    runs_dir = research_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    log.debug("research_dir_ensured", path=str(research_dir))
    return research_dir


def create_run_dir(project_path: Path, cycle_id: str) -> Path:
    """Create and return ``.factory/research/runs/<cycle_id>/``."""
    run_dir = project_path / ".factory" / "research" / "runs" / cycle_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log.debug("run_dir_created", path=str(run_dir), cycle_id=cycle_id)
    return run_dir


def save_run_summary(run_dir: Path, summary: dict) -> None:
    """Write ``summary.json`` to the given run directory."""
    path = run_dir / "summary.json"
    path.write_text(json.dumps(summary, indent=2, default=str))
    log.debug("run_summary_saved", path=str(path))


def load_run_summary(run_dir: Path) -> dict | None:
    """Load ``summary.json`` from the given run directory, or return None."""
    path = run_dir / "summary.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def list_runs(project_path: Path) -> list[Path]:
    """List all run directories sorted by name."""
    runs_dir = project_path / ".factory" / "research" / "runs"
    if not runs_dir.exists():
        return []
    return sorted(p for p in runs_dir.iterdir() if p.is_dir())


def write_comparison(
    project_path: Path, current_id: str, previous_id: str, comparison: str
) -> None:
    """Write a comparison report between two runs."""
    research_dir = ensure_research_dir(project_path)
    path = research_dir / f"comparison_{previous_id}_vs_{current_id}.md"
    path.write_text(comparison)
    log.debug(
        "comparison_written",
        path=str(path),
        current=current_id,
        previous=previous_id,
    )


# ── run execution ────────────────────────────────────────────────


async def execute_run(
    project_path: Path, config: ResearchTarget, cycle_id: str
) -> RunResult:
    """Execute the run_command from config and return a RunResult."""
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
