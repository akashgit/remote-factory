#!/usr/bin/env python3
"""Eval script for the Remote Factory.

Runs 6 project-specific (hygiene) evaluation dimensions and outputs JSON
to stdout. The factory's eval runner injects universal growth dimensions
on top of these, so this script only needs to cover project health.

Output format:
    {"results": [{"name": str, "score": float, "weight": float, "passed": bool, "details": str}, ...]}

Each dimension parses real metrics from tool output rather than using
binary exit-code checks.
"""

import asyncio
import json
import os
import re
import subprocess
import sys

# Ensure the project root is on sys.path so factory.* imports work.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

_EVAL_TIMEOUT = int(os.environ.get("FACTORY_EVAL_TIMEOUT", "300"))


# ── Dimension 1: tests (weight 0.30) ─────────────────────────────


def eval_tests() -> dict:
    """Run test suite and parse pass/fail counts."""
    try:
        result = subprocess.run(
            ["uv", "run", "pytest", "-v"],
            capture_output=True,
            text=True,
            timeout=_EVAL_TIMEOUT,
            cwd=PROJECT_ROOT,
        )
        output = result.stdout + result.stderr

        # Look for "X passed" and optionally "Y failed"
        passed_match = re.search(r"(\d+)\s+passed", output)
        failed_match = re.search(r"(\d+)\s+failed", output)

        passed_count = int(passed_match.group(1)) if passed_match else 0
        failed_count = int(failed_match.group(1)) if failed_match else 0
        total = passed_count + failed_count

        if total == 0:
            score = 0.0
            ok = False
            details = "No test results found in output"
        else:
            score = passed_count / total
            ok = failed_count == 0
            details = f"{passed_count} passed, {failed_count} failed"

        return {
            "name": "tests",
            "score": round(score, 4),
            "weight": 0.30,
            "passed": ok,
            "details": details,
        }
    except subprocess.TimeoutExpired:
        return {
            "name": "tests",
            "score": 0.0,
            "weight": 0.30,
            "passed": False,
            "details": f"Timed out after {_EVAL_TIMEOUT}s",
        }
    except Exception as exc:
        return {
            "name": "tests",
            "score": 0.0,
            "weight": 0.30,
            "passed": False,
            "details": f"Error: {exc}",
        }


# ── Dimension 2: lint (weight 0.15) ──────────────────────────────


def eval_lint() -> dict:
    """Run ruff and parse error count."""
    try:
        result = subprocess.run(
            ["uv", "run", "ruff", "check", "."],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=PROJECT_ROOT,
        )
        if result.returncode == 0:
            return {
                "name": "lint",
                "score": 1.0,
                "weight": 0.15,
                "passed": True,
                "details": "No lint errors",
            }

        # Parse "Found X error(s)"
        output = result.stdout + result.stderr
        error_match = re.search(r"Found\s+(\d+)\s+error", output)
        if error_match:
            error_count = int(error_match.group(1))
            # Partial credit: lose 0.1 per error, floor at 0
            score = max(0.0, 1.0 - error_count * 0.1)
            details = f"Found {error_count} lint error(s)"
        else:
            score = 0.0
            details = output.strip()[-500:]

        return {
            "name": "lint",
            "score": round(score, 4),
            "weight": 0.15,
            "passed": False,
            "details": details,
        }
    except subprocess.TimeoutExpired:
        return {
            "name": "lint",
            "score": 0.0,
            "weight": 0.15,
            "passed": False,
            "details": "Timed out after 120s",
        }
    except Exception as exc:
        return {
            "name": "lint",
            "score": 0.0,
            "weight": 0.15,
            "passed": False,
            "details": f"Error: {exc}",
        }


# ── Dimension 3: type_check (weight 0.10) ────────────────────────


def eval_type_check() -> dict:
    """Run mypy and parse error count."""
    try:
        result = subprocess.run(
            ["uv", "run", "mypy", "factory/"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=PROJECT_ROOT,
        )
        if result.returncode == 0:
            return {
                "name": "type_check",
                "score": 1.0,
                "weight": 0.10,
                "passed": True,
                "details": "No type errors",
            }

        output = result.stdout + result.stderr
        # mypy prints "Found X error(s)" at the end
        error_match = re.search(r"Found\s+(\d+)\s+error", output)
        if error_match:
            error_count = int(error_match.group(1))
            score = max(0.0, 1.0 - error_count * 0.05)
            details = f"Found {error_count} type error(s)"
        else:
            score = 0.0
            details = output.strip()[-500:]

        return {
            "name": "type_check",
            "score": round(score, 4),
            "weight": 0.10,
            "passed": False,
            "details": details,
        }
    except subprocess.TimeoutExpired:
        return {
            "name": "type_check",
            "score": 0.0,
            "weight": 0.10,
            "passed": False,
            "details": "Timed out after 120s",
        }
    except Exception as exc:
        return {
            "name": "type_check",
            "score": 0.0,
            "weight": 0.10,
            "passed": False,
            "details": f"Error: {exc}",
        }


# ── Dimension 4: coverage (weight 0.25) ──────────────────────────


def eval_coverage() -> dict:
    """Run pytest with coverage and parse the TOTAL percentage."""
    try:
        result = subprocess.run(
            ["uv", "run", "pytest", "--cov=factory", "--cov-report=term", "-q"],
            capture_output=True,
            text=True,
            timeout=_EVAL_TIMEOUT,
            cwd=PROJECT_ROOT,
        )
        output = result.stdout + result.stderr

        # Parse TOTAL line: "TOTAL    123    30    75%"
        total_match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", output)
        if total_match:
            percentage = int(total_match.group(1))
            score = percentage / 100.0
            ok = percentage >= 80
            details = f"Coverage: {percentage}% (threshold: 80%)"
        else:
            score = 0.0
            ok = False
            details = "Could not parse coverage from output"

        return {
            "name": "coverage",
            "score": round(score, 4),
            "weight": 0.25,
            "passed": ok,
            "details": details,
        }
    except subprocess.TimeoutExpired:
        return {
            "name": "coverage",
            "score": 0.0,
            "weight": 0.25,
            "passed": False,
            "details": f"Timed out after {_EVAL_TIMEOUT}s",
        }
    except Exception as exc:
        return {
            "name": "coverage",
            "score": 0.0,
            "weight": 0.25,
            "passed": False,
            "details": f"Error: {exc}",
        }


# ── Dimension 5: guard_patterns (weight 0.10) ────────────────────


def eval_guard_patterns() -> dict:
    """Test that the guard system's glob matching works correctly."""
    try:
        from factory.eval.guards import _glob_match
    except (ImportError, AttributeError) as exc:
        return {
            "name": "guard_patterns",
            "score": 0.0,
            "weight": 0.10,
            "passed": False,
            "details": f"Could not import _glob_match: {exc}",
        }

    try:
        test_cases: list[tuple[str, str, bool]] = [
            ("factory/**/*.py", "factory/eval/runner.py", True),
            ("factory/**/*.py", "tests/test_guards.py", False),
            ("tests/**/*.py", "tests/test_guards.py", True),
            ("templates/**", "templates/factory_config.md", True),
        ]

        correct = 0
        results_detail: list[str] = []
        for pattern, filepath, expected in test_cases:
            actual = _glob_match(filepath, pattern)
            if actual == expected:
                correct += 1
                results_detail.append(f"OK: {pattern} vs {filepath}")
            else:
                results_detail.append(
                    f"FAIL: {pattern} vs {filepath} — "
                    f"expected {expected}, got {actual}"
                )

        total = len(test_cases)
        score = correct / total
        ok = correct == total

        return {
            "name": "guard_patterns",
            "score": round(score, 4),
            "weight": 0.10,
            "passed": ok,
            "details": "; ".join(results_detail),
        }
    except Exception as exc:
        return {
            "name": "guard_patterns",
            "score": 0.0,
            "weight": 0.10,
            "passed": False,
            "details": f"Error running guard pattern tests: {exc}",
        }


# ── Dimension 6: config_parser (weight 0.10) ─────────────────────


def eval_config_parser() -> dict:
    """Test that the factory.md parser extracts fields correctly."""
    try:
        from factory.store import ExperimentStore
    except ImportError as exc:
        return {
            "name": "config_parser",
            "score": 0.0,
            "weight": 0.10,
            "passed": False,
            "details": f"Could not import ExperimentStore: {exc}",
        }

    try:
        from pathlib import Path

        store = ExperimentStore(Path(PROJECT_ROOT))
        config = asyncio.run(store.reparse_config())

        checks: list[tuple[str, bool]] = []

        # goal should be non-empty
        checks.append(("goal is non-empty", bool(config.goal and len(config.goal) > 0)))

        # scope should contain expected patterns
        checks.append((
            "scope contains factory/**/*.py",
            "factory/**/*.py" in config.scope,
        ))

        # eval_command should reference eval/score.py
        checks.append((
            "eval_command references score.py",
            "eval/score.py" in config.eval_command,
        ))

        # eval_threshold should be 0.8
        checks.append(("eval_threshold is 0.8", config.eval_threshold == 0.8))

        correct = sum(1 for _, ok in checks if ok)
        total = len(checks)
        score = correct / total
        ok = correct == total

        details_parts = [
            f"{'OK' if passed else 'FAIL'}: {label}"
            for label, passed in checks
        ]

        return {
            "name": "config_parser",
            "score": round(score, 4),
            "weight": 0.10,
            "passed": ok,
            "details": "; ".join(details_parts),
        }
    except Exception as exc:
        return {
            "name": "config_parser",
            "score": 0.0,
            "weight": 0.10,
            "passed": False,
            "details": f"Error running config parser tests: {exc}",
        }


# ── Main ──────────────────────────────────────────────────────────

EVALS = [
    eval_tests,
    eval_lint,
    eval_type_check,
    eval_coverage,
    eval_guard_patterns,
    eval_config_parser,
]


def main() -> None:
    results = [fn() for fn in EVALS]
    output = {"results": results}
    json.dump(output, sys.stdout, indent=2)
    print()  # trailing newline


if __name__ == "__main__":
    main()
