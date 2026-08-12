"""Evaluate solutions using the Harbor verifier."""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def evaluate_rollouts(rollouts: list[dict[str, Any]], task_dir: Path) -> list[float]:
    """Evaluate all rollouts using the verifier from tests/test.sh.

    Args:
        rollouts: List of rollout dicts
        task_dir: Path to the Harbor task directory

    Returns:
        List of scores (one per rollout)
    """
    scores = []

    for rollout in rollouts:
        score = evaluate_one_solution(rollout["solution"], task_dir)
        scores.append(score)

    return scores


def evaluate_one_solution(solution: dict[str, Any], task_dir: Path) -> float:
    """Evaluate a single solution using the verifier.

    Args:
        solution: Solution dict (e.g., {"circles": [[x, y, r], ...]})
        task_dir: Path to the Harbor task directory

    Returns:
        Score (float), or -inf if evaluation failed
    """
    # Extract verifier code from tests/test.sh
    test_sh = task_dir / "tests" / "test.sh"

    if not test_sh.exists():
        raise FileNotFoundError(f"Verifier not found: {test_sh}")

    # Run the verifier in a temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        workspace = tmpdir_path / "workspace"
        workspace.mkdir()

        # Write solution.json
        solution_file = workspace / "solution.json"
        with open(solution_file, "w") as f:
            json.dump(solution, f)

        # Run test.sh
        try:
            subprocess.run(
                ["bash", str(test_sh)],
                cwd=tmpdir_path,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,  # Don't raise on non-zero exit; we check score.txt instead
            )

            # Read score.txt
            score_file = workspace / "score.txt"
            if score_file.exists():
                score = float(score_file.read_text().strip())
                return score
            else:
                # Verifier failed, return penalty
                print("WARNING: No score.txt produced for solution")
                return float("-inf")

        except Exception as e:
            print(f"ERROR evaluating solution: {e}")
            return float("-inf")
