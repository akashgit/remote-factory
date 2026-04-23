"""EvalRunner — compute mandatory dimensions and merge with project-specific evals.

The factory's eval system has mandatory dimensions that apply to every project:
  - 6 hygiene dimensions (tests, lint, type_check, coverage, guard_patterns, config_parser)
  - 5 growth dimensions (capability_surface, experiment_diversity, observability,
    research_grounding, factory_effectiveness)

Projects can ADD dimensions via eval/score.py and via project_eval in factory.md.
The mandatory dimensions are computed by the factory itself, not by per-project scripts.

Weight distribution (configurable via factory.md ## Eval Weights):
  - No project eval: 50% hygiene + 50% growth (default)
  - With project eval: configurable, default 30% hygiene + 20% growth + 50% project
"""

import asyncio
import json
import os
from pathlib import Path

from factory.eval.growth import compute_growth_results
from factory.eval.hygiene import compute_hygiene_results
from factory.eval.scorer import compute_composite
from factory.models import CompositeScore, EvalResult, EvalWeights, ProjectEvalDimension


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


def _effective_weights(
    eval_weights: EvalWeights,
    has_custom_project: bool,
) -> tuple[float, float, float]:
    """Determine effective hygiene/growth/project weight split."""
    if not has_custom_project:
        return 0.50, 0.50, 0.0

    if eval_weights.project > 0:
        total = eval_weights.hygiene + eval_weights.growth + eval_weights.project
        return (
            eval_weights.hygiene / total,
            eval_weights.growth / total,
            eval_weights.project / total,
        )

    # Has project eval but user didn't set weights → auto-distribute
    return 0.30, 0.20, 0.50


def _normalize_tier(
    results: list[EvalResult],
    target_weight: float,
) -> list[EvalResult]:
    """Normalize a list of EvalResults so their weights sum to target_weight."""
    if not results or target_weight <= 0:
        return []
    weight_sum = sum(r.weight for r in results)
    if weight_sum <= 0:
        return results
    return [
        EvalResult(
            name=r.name,
            score=r.score,
            weight=(r.weight / weight_sum) * target_weight,
            passed=r.passed,
            details=r.details,
        )
        for r in results
    ]


def _merge_all(
    hygiene_results: list[EvalResult],
    project_results: list[EvalResult],
    growth_results: list[EvalResult],
    custom_project_results: list[EvalResult] | None = None,
    eval_weights: EvalWeights | None = None,
) -> list[EvalResult]:
    """Merge mandatory hygiene + project additions + mandatory growth + custom project eval.

    Weight distribution (three tiers):
      - Hygiene (mandatory 6 + eval/score.py additions): configurable (default 50%)
      - Growth (mandatory 5): configurable (default 50%)
      - Project eval (user-defined in factory.md): configurable (default 0%, auto 50% when present)

    When custom_project_results is non-empty, weights shift to accommodate project eval.
    """
    mandatory_names = {r.name for r in hygiene_results} | {r.name for r in growth_results}
    additional = [r for r in project_results if r.name not in mandatory_names]
    all_hygiene = list(hygiene_results) + additional

    custom = custom_project_results or []
    weights = eval_weights or EvalWeights()
    h_w, g_w, p_w = _effective_weights(weights, bool(custom))

    normalized_hygiene = _normalize_tier(all_hygiene, h_w)
    normalized_growth = _normalize_tier(growth_results, g_w)
    normalized_project = _normalize_tier(custom, p_w)

    return normalized_hygiene + normalized_growth + normalized_project


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


async def _run_single_project_dimension(
    dim: ProjectEvalDimension,
    project_path: Path,
) -> EvalResult:
    """Run a single user-defined project eval dimension."""
    parts = dim.command.split()
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
            proc.communicate(), timeout=dim.timeout
        )
    except asyncio.TimeoutError:
        proc.kill()  # type: ignore[union-attr]
        await proc.wait()  # type: ignore[union-attr]
        return EvalResult(
            name=dim.name, score=0.0, weight=dim.weight,
            passed=False, details=f"Timed out after {dim.timeout}s",
        )
    except FileNotFoundError:
        return EvalResult(
            name=dim.name, score=0.0, weight=dim.weight,
            passed=False, details=f"Command not found: {parts[0]}",
        )

    stdout = stdout_bytes.decode()
    stderr = stderr_bytes.decode()

    if dim.parse == "json":
        try:
            data = json.loads(stdout)
            raw_score = float(data.get("score", 0.0))
            score = max(0.0, min(1.0, raw_score))
            return EvalResult(
                name=dim.name, score=score, weight=dim.weight,
                passed=score >= 0.5,
                details=str(data.get("details", stdout[:500])),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return EvalResult(
                name=dim.name, score=0.0, weight=dim.weight,
                passed=False, details=f"Invalid JSON: {stdout[:200]}",
            )

    # exit_code parse mode
    passed = proc.returncode == 0
    return EvalResult(
        name=dim.name, score=1.0 if passed else 0.0, weight=dim.weight,
        passed=passed, details=(stdout or stderr).strip()[-500:],
    )


async def _run_custom_project_eval(
    dimensions: list[ProjectEvalDimension],
    project_path: Path,
) -> list[EvalResult]:
    """Run all user-defined project eval dimensions from factory.md."""
    results = []
    for dim in dimensions:
        result = await _run_single_project_dimension(dim, project_path)
        results.append(result)
    return results


async def run_eval(
    eval_command: str,
    project_path: Path,
    threshold: float,
    timeout: float = 120.0,
    project_eval: list[ProjectEvalDimension] | None = None,
    eval_weights: EvalWeights | None = None,
    skip_project_eval: bool = False,
) -> CompositeScore:
    """Compute mandatory dimensions + project-specific additions + custom project eval.

    1. Compute 6 mandatory hygiene dimensions (auto-detect project tooling)
    2. Run project's eval/score.py for additional dimensions (optional)
    3. Compute 5 mandatory growth dimensions
    4. Run custom project eval dimensions from factory.md (if configured)
    5. Merge all with configurable weight split
    6. Return composite score
    """
    # Step 1: Mandatory hygiene (always runs)
    hygiene_dicts = compute_hygiene_results(project_path)
    hygiene_results = [EvalResult(**r) for r in hygiene_dicts]

    # Step 2: Project-specific additions (optional, additive only)
    project_results = await _run_project_eval(eval_command, project_path, timeout)

    # Step 3: Mandatory growth (always runs)
    growth_dicts = compute_growth_results(project_path)
    growth_results = [EvalResult(**r) for r in growth_dicts]

    # Step 4: Custom project eval (user-defined in factory.md)
    custom_results: list[EvalResult] = []
    if project_eval and not skip_project_eval:
        custom_results = await _run_custom_project_eval(project_eval, project_path)

    # Step 5: Merge all dimensions with weight split
    merged = _merge_all(
        hygiene_results, project_results, growth_results,
        custom_project_results=custom_results,
        eval_weights=eval_weights,
    )

    # Step 6: Compute composite
    score = compute_composite(merged, guard_violations=[], threshold=threshold)

    # Step 7: Save results to .factory/last_eval.json for dashboard consumption
    last_eval_path = project_path / ".factory" / "last_eval.json"
    if last_eval_path.parent.exists():
        try:
            last_eval_path.write_text(json.dumps(score.model_dump(), indent=2))
        except OSError:
            pass

    return score
