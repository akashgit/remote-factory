#!/usr/bin/env python3
"""Extract the best score from Lumen evaluation results for outer loop scoring.

Searches .factory/lumen/.running/ for evaluation_results.json files across all
iterations, finds the global best score, and outputs JSON to stdout in the
format expected by the outer loop's JSONEvaluator:

    {"score": <float>, "valid": <bool>}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def find_best_score(run_dir: Path) -> tuple[float, bool]:
    """Scan all iteration directories for the best overall score."""
    best_score = 0.0
    found_any = False

    for iteration_dir in sorted(run_dir.glob("iteration_*")):
        eval_file = iteration_dir / "evaluation_results.json"
        if not eval_file.exists():
            continue

        try:
            with open(eval_file) as f:
                results = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        overall = results.get("overall", results)
        score = overall.get("best_score")
        if score is None:
            continue

        found_any = True
        if score > best_score:
            best_score = score

    return best_score, found_any


def main() -> None:
    run_dir = Path(".factory/lumen/.running")

    if not run_dir.exists():
        json.dump({"score": 0.0, "valid": False}, sys.stdout)
        sys.exit(0)

    best_score, found_any = find_best_score(run_dir)
    json.dump({"score": best_score, "valid": found_any}, sys.stdout)


if __name__ == "__main__":
    main()
