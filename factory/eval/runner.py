"""EvalRunner — compute mandatory dimensions and merge with project-specific evals.

The factory's eval system has 11 mandatory dimensions that apply to every project:
  - 6 hygiene dimensions (tests, lint, type_check, coverage, guard_patterns, config_parser)
  - 5 growth dimensions (capability_surface, experiment_diversity, observability,
    research_grounding, factory_effectiveness)

Projects can ADD dimensions via eval/score.py but cannot remove any of the 11.
The mandatory dimensions are computed by the factory itself, not by per-project scripts.
"""

import asyncio
import json
import os
from pathlib import Path

from factory.eval.growth import compute_growth_results
from factory.eval.hygiene import compute_hygiene_results
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


def _merge_all(
    hygiene_results: list[EvalResult],
    project_results: list[EvalResult],
    growth_results: list[EvalResult],
) -> list[EvalResult]:
    """Merge mandatory hygiene + project-specific additions + mandatory growth.

    Weight distribution:
      - Hygiene (mandatory 6): 50% of composite
      - Growth (mandatory 5): 50% of composite
      - Project-specific additions: bonus dimensions, normalized into the hygiene half

    If the project's eval/score.py returns dimensions with the same name as a
    mandatory dimension, the project version is ignored (mandatory wins).
    """
    # Names of mandatory dimensions — project can't override these
    mandatory_names = {r.name for r in hygiene_results} | {r.name for r in growth_results}

    # Filter project results to only truly additional dimensions
    additional = [r for r in project_results if r.name not in mandatory_names]

    # Normalize hygiene weights to sum to 0.50
    all_hygiene = list(hygiene_results) + additional
    hygiene_weight_sum = sum(r.weight for r in all_hygiene)
    if hygiene_weight_sum > 0:
        normalized_hygiene = [
            EvalResult(
                name=r.name,
                score=r.score,
                weight=(r.weight / hygiene_weight_sum) * 0.50,
                passed=r.passed,
                details=r.details,
            )
            for r in all_hygiene
        ]
    else:
        normalized_hygiene = all_hygiene

    # Normalize growth weights to sum to 0.50
    growth_weight_sum = sum(r.weight for r in growth_results)
    if growth_weight_sum > 0:
        normalized_growth = [
            EvalResult(
                name=r.name,
                score=r.score,
                weight=(r.weight / growth_weight_sum) * 0.50,
                passed=r.passed,
                details=r.details,
            )
            for r in growth_results
        ]
    else:
        normalized_growth = growth_results

    return normalized_hygiene + normalized_growth


async def _run_project_eval(
    eval_command: str,
    project_path: Path,
    timeout: float = 120.0,
) -> list[EvalResult]:
    """Run the project's eval/score.py (if it exists) and return additional results.

    Returns an empty list if the command fails or returns no results.
    These are project-specific ADDITIONS to the mandatory 11 dimensions.
    """
    parts = eval_command.split()

    # Clean environment
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
        return []
    except FileNotFoundError:
        return []

    if proc.returncode != 0:
        return []

    stdout = stdout_bytes.decode()
    try:
        data = json.loads(stdout)
        return [EvalResult(**r) for r in data["results"]]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


async def run_eval(
    eval_command: str,
    project_path: Path,
    threshold: float,
    timeout: float = 120.0,
) -> CompositeScore:
    """Compute all 11 mandatory dimensions + any project-specific additions.

    1. Compute 6 mandatory hygiene dimensions (auto-detect project tooling)
    2. Run project's eval/score.py for additional dimensions (optional)
    3. Compute 5 mandatory growth dimensions
    4. Merge all with 50/50 hygiene/growth split
    5. Return composite score
    """
    # Step 1: Mandatory hygiene (always runs)
    hygiene_dicts = compute_hygiene_results(project_path)
    hygiene_results = [EvalResult(**r) for r in hygiene_dicts]

    # Step 2: Project-specific additions (optional, additive only)
    project_results = await _run_project_eval(eval_command, project_path, timeout)

    # Step 3: Mandatory growth (always runs)
    growth_dicts = compute_growth_results(project_path)
    growth_results = [EvalResult(**r) for r in growth_dicts]

    # Step 4: Merge all dimensions
    merged = _merge_all(hygiene_results, project_results, growth_results)

    # Step 5: Compute composite
    score = compute_composite(merged, guard_violations=[], threshold=threshold)

    # Step 6: Save results to .factory/last_eval.json for dashboard consumption
    last_eval_path = project_path / ".factory" / "last_eval.json"
    if last_eval_path.parent.exists():
        try:
            last_eval_path.write_text(json.dumps(score.model_dump(), indent=2))
        except OSError:
            pass

    return score
