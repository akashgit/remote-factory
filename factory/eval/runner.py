"""EvalRunner — run eval commands as subprocesses and parse results."""

import asyncio
import json
import os
from pathlib import Path

from factory.eval.scorer import compute_composite
from factory.models import CompositeScore, EvalResult


def _error_score(message: str, details: str = "") -> CompositeScore:
    """Return a CompositeScore representing an error."""
    return CompositeScore(
        total=0.0,
        results=[
            EvalResult(
                name="error",
                score=0.0,
                weight=1.0,
                passed=False,
                details=details or message,
            )
        ],
        guard_violations=[],
        passed=False,
    )


async def run_eval(
    eval_command: str,
    project_path: Path,
    threshold: float,
    timeout: float = 120.0,
) -> CompositeScore:
    """Run eval_command in project_path, parse JSON stdout, return CompositeScore.

    Expected JSON format: {"results": [{"name", "score", "weight", "passed", "details"}, ...]}
    """
    parts = eval_command.split()

    # Clean environment: remove VIRTUAL_ENV so the target project's own
    # venv is used (prevents mypy/pytest from checking wrong packages).
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}

    try:
        proc = await asyncio.create_subprocess_exec(
            *parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_path,
            env=env,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()  # type: ignore[union-attr]
        await proc.wait()  # type: ignore[union-attr]
        return _error_score("Eval timed out", f"Timeout after {timeout}s")
    except FileNotFoundError as e:
        return _error_score("Eval command not found", str(e))

    stdout = stdout_bytes.decode()
    stderr = stderr_bytes.decode()

    if proc.returncode != 0:
        return _error_score(
            "Eval exited with non-zero status",
            f"exit code {proc.returncode}: {stderr}",
        )

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return _error_score("Invalid JSON output", stdout[:500])

    try:
        results = [EvalResult(**r) for r in data["results"]]
    except (KeyError, TypeError, Exception) as e:
        return _error_score("Failed to parse eval results", str(e))

    return compute_composite(results, guard_violations=[], threshold=threshold)
