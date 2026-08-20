"""Pytest output evaluator — partial credit scoring from pytest results.

Moved from featurebench_evaluator.py. Parses pytest-json-report output
or factory eval artifacts for per-test pass/fail fraction scoring.
"""

from __future__ import annotations

import json
from pathlib import Path

from factory.inner_loop import EvalResult


class PytestEvaluator:
    """Parses pytest output for partial credit scoring.

    Looks for pytest-json-report output files (report.json) or factory
    eval artifacts. Computes score as fraction of tests passing.
    """

    def __init__(self, benchmark: str = "featurebench", **kwargs: object) -> None:
        self.benchmark = benchmark

    def parse(self, artifact_path: Path) -> EvalResult:
        try:
            data = json.loads(Path(artifact_path).read_text(errors="replace"))
        except (json.JSONDecodeError, OSError):
            return EvalResult(score=0.0, valid=False)

        score, metrics = self._extract_partial_credit(data)
        return EvalResult(
            score=score,
            metrics=metrics,
            valid=True,
            artifacts=[str(artifact_path)],
        )

    def parse_many(self, artifact_paths: list[Path]) -> EvalResult:
        best = EvalResult(score=0.0, valid=False)
        for p in artifact_paths:
            result = self.parse(p)
            if result.score > best.score:
                best = result
        return best

    def get_info(self) -> dict:
        return {
            "benchmark": self.benchmark,
            "scoring": "partial_credit",
            "test_format": "pytest",
            "metrics": ["tests_passed", "tests_total", "pass_rate"],
        }

    def _extract_partial_credit(self, data: dict) -> tuple[float, dict[str, float]]:
        if "tests" in data:
            return self._parse_pytest_json_report(data)
        if "results" in data:
            return self._parse_factory_eval(data)
        if "summary" in data:
            summary = data["summary"]
            passed = summary.get("passed", 0)
            total = summary.get("total", 0)
            if total > 0:
                score = passed / total
                return score, {
                    "tests_passed": float(passed),
                    "tests_total": float(total),
                    "pass_rate": score,
                }
        score = float(data.get("score", data.get("combined_score", 0.0)))
        return score, {"raw_score": score}

    def _parse_pytest_json_report(self, data: dict) -> tuple[float, dict[str, float]]:
        tests = data.get("tests", [])
        if not tests:
            return 0.0, {"tests_passed": 0.0, "tests_total": 0.0, "pass_rate": 0.0}
        passed = sum(1 for t in tests if t.get("outcome") == "passed")
        total = len(tests)
        score = passed / total if total > 0 else 0.0
        return score, {
            "tests_passed": float(passed),
            "tests_total": float(total),
            "pass_rate": score,
        }

    def _parse_factory_eval(self, data: dict) -> tuple[float, dict[str, float]]:
        results = data.get("results", [])
        if not results:
            return 0.0, {}
        scores = [float(r.get("score", 0.0)) for r in results if "score" in r]
        if not scores:
            return 0.0, {}
        avg = sum(scores) / len(scores)
        return avg, {
            "avg_score": avg,
            "max_score": max(scores),
            "min_score": min(scores),
            "num_results": float(len(scores)),
        }
