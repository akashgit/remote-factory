"""Evaluate solutions using Einstein Arena verifiers."""

import importlib.util
import sys
from pathlib import Path
from typing import Any


def load_verifier(task_dir: Path):
    """Dynamically import a task's verifier.py and return its evaluate function."""
    verifier_path = (task_dir / "verifier.py").resolve()
    if not verifier_path.exists():
        raise FileNotFoundError(f"Verifier not found: {verifier_path}")

    spec = importlib.util.spec_from_file_location("verifier", verifier_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["verifier"] = module
    spec.loader.exec_module(module)
    return module.evaluate


def evaluate_rollouts(
    rollouts: list[dict[str, Any]],
    task_dir: Path,
    *,
    direction: str = "maximize",
    reward_cfg: dict | None = None,
) -> list[dict[str, float]]:
    """Evaluate all rollouts using the task's verifier and shape rewards.

    Args:
        rollouts: List of rollout dicts
        task_dir: Path to the Einstein Arena task directory
        direction: "maximize" or "minimize"
        reward_cfg: Per-task reward shaping config (optional)

    Returns:
        List of dicts with "raw_score" and "score" (shaped reward)
    """
    from factory.lumen.reward import shape_reward

    evaluate_fn = load_verifier(task_dir)
    results = []

    for rollout in rollouts:
        raw = evaluate_one_solution(rollout["solution"], evaluate_fn)
        shaped = shape_reward(raw, direction, reward_cfg)
        results.append({"raw_score": raw, "score": shaped})

    return results


def evaluate_one_solution(solution: dict[str, Any], evaluate_fn) -> float:
    """Evaluate a single solution using the verifier's evaluate function.

    Args:
        solution: Solution dict (e.g., {"circles": [[x, y, r], ...]})
        evaluate_fn: The task's evaluate(data) -> float function

    Returns:
        Score (float), or -inf if evaluation failed
    """
    try:
        return float(evaluate_fn(solution))
    except Exception as e:
        print(f"ERROR evaluating solution: {e}")
        return float("-inf")
