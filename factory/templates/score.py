#!/usr/bin/env python3
"""Template eval script for the Remote Factory.

This script is the entry point the factory calls to evaluate a change.
It must print a JSON object to stdout with this shape:

    {"results": [{"name": str, "score": float, "weight": float, "passed": bool, "details": str}, ...]}

Each function below runs one eval and returns a dict. Add your own
project-specific evals by following the same pattern.

Usage:
    python eval/score.py

This script is standalone — it does NOT import anything from the factory package.
"""

import json
import subprocess
import sys


def eval_tests() -> dict:
    """Run the test suite and score based on pass/fail."""
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "--tb=short", "-q"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        passed = result.returncode == 0
        return {
            "name": "tests",
            "score": 1.0 if passed else 0.0,
            "weight": 0.5,
            "passed": passed,
            "details": result.stdout.strip()[-500:] if result.stdout else result.stderr.strip()[-500:],
        }
    except subprocess.TimeoutExpired:
        return {
            "name": "tests",
            "score": 0.0,
            "weight": 0.5,
            "passed": False,
            "details": "Test suite timed out after 300s",
        }


def eval_lint() -> dict:
    """Run the linter and score based on clean output."""
    try:
        result = subprocess.run(
            ["python", "-m", "ruff", "check", "."],
            capture_output=True,
            text=True,
            timeout=60,
        )
        passed = result.returncode == 0
        lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
        violation_count = max(0, len(lines) - 1)
        score = 1.0 if passed else max(0.0, 1.0 - (violation_count * 0.1))
        return {
            "name": "lint",
            "score": score,
            "weight": 0.3,
            "passed": passed,
            "details": result.stdout.strip()[-500:] if result.stdout else "No output",
        }
    except subprocess.TimeoutExpired:
        return {
            "name": "lint",
            "score": 0.0,
            "weight": 0.3,
            "passed": False,
            "details": "Linter timed out after 60s",
        }


# Register all eval functions here.
EVALS = [eval_tests, eval_lint]


def main() -> None:
    results = [fn() for fn in EVALS]
    output = {"results": results}
    json.dump(output, sys.stdout, indent=2)
    print()  # trailing newline


if __name__ == "__main__":
    main()
