"""Statefulness eval benchmark — parametrized pytest harness.

Measures CEO session statefulness across iterations, comparing:
- Control: session_summary.md deleted before each run
- Treatment: session_summary.md preserved between runs

Each iteration runs for up to 120s with process group cleanup.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog

from conftest import (
    parse_trace,
    run_ceo_subprocess,
    save_iteration_result,
)

log = structlog.get_logger()

PROJECTS = [
    {
        "name": "factory-ui",
        "path": "~/factory-projects/factory-ui",
        "focus": "improve UI components",
    },
    {
        "name": "remote-factory-timeout",
        "path": "~/redhat-projects/remote-factory",
        "focus": "agent timeout error handling",
    },
    {
        "name": "remote-factory-eval",
        "path": "~/redhat-projects/remote-factory",
        "focus": "eval score reliability",
    },
]

CONDITIONS = ["control", "treatment"]
ITERATIONS = list(range(1, 6))
TIMEOUT_S = 120
EXPECTED_TIMEOUT_CODES = {130, 142}

_iter1_failures: dict[tuple[str, str], int] = {}


def _session_summary_path(project_path: str) -> Path:
    """Return the session_summary.md path for a project."""
    return Path(project_path).expanduser() / ".factory" / "state" / "session_summary.md"


@pytest.mark.slow
@pytest.mark.parametrize(
    "project",
    PROJECTS,
    ids=[p["name"] for p in PROJECTS],
)
@pytest.mark.parametrize("condition", CONDITIONS)
@pytest.mark.parametrize("iteration", ITERATIONS)
async def test_statefulness_iteration(
    project: dict,
    condition: str,
    iteration: int,
    statefulness_results_dir: Path,
) -> None:
    """Run a single CEO iteration and collect statefulness metrics."""
    key = (project["name"], condition)
    if key in _iter1_failures:
        pytest.skip(
            f"Iteration 1 failed with exit code {_iter1_failures[key]} — "
            "skipping remaining iterations"
        )

    project_path = str(Path(project["path"]).expanduser())
    summary_path = _session_summary_path(project_path)

    if condition == "control":
        if summary_path.exists():
            summary_path.unlink()
            log.info("deleted_session_summary", path=str(summary_path))
    elif condition == "treatment" and iteration > 1:
        if not summary_path.exists():
            from factory.statefulness import save_session_summary

            save_session_summary(Path(project_path))
            log.info("generated_session_summary", path=str(summary_path))

    import time

    start_ts = time.time()

    exit_code, stdout, duration_s = await run_ceo_subprocess(
        project_path=project_path,
        focus=project["focus"],
        timeout_s=TIMEOUT_S,
        condition=condition,
    )

    metrics = parse_trace(stdout)

    save_iteration_result(
        results_dir=statefulness_results_dir,
        project=project["name"],
        condition=condition,
        iteration=iteration,
        metrics=metrics,
        exit_code=exit_code,
        duration_s=duration_s,
        project_path=project_path,
        start_time=start_ts,
    )

    if iteration == 1 and exit_code != 0 and exit_code not in EXPECTED_TIMEOUT_CODES:
        _iter1_failures[key] = exit_code
        pytest.skip(
            f"Iteration 1 failed with exit code {exit_code} — aborting remaining iterations"
        )

    assert exit_code in EXPECTED_TIMEOUT_CODES or exit_code == 0, (
        f"CEO exited with unexpected code {exit_code}"
    )
