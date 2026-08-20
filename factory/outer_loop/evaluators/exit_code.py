"""Exit code evaluator — binary pass/fail from subprocess return code.

Used by benchmarks like SWE-bench where success is determined by
whether the test command exits with code 0.
"""

from __future__ import annotations

import json
from pathlib import Path

from factory.inner_loop import EvalResult


class ExitCodeEvaluator:
    """Binary pass/fail scoring from subprocess exit code.

    Reads an artifact JSON with {"returncode": N, ...}.
    Score 1.0 if returncode is 0, else 0.0.
    """

    def __init__(self, **kwargs: object) -> None:
        pass

    def parse(self, artifact_path: Path) -> EvalResult:
        try:
            data = json.loads(Path(artifact_path).read_text(errors="replace"))
        except (json.JSONDecodeError, OSError):
            return EvalResult(score=0.0, valid=False)

        returncode = data.get("returncode")
        if returncode is None:
            return EvalResult(score=0.0, valid=False)

        score = 1.0 if returncode == 0 else 0.0
        return EvalResult(
            score=score,
            metrics={"returncode": float(returncode), "passed": score},
            valid=True,
            artifacts=[str(artifact_path)],
        )

    def parse_many(self, artifact_paths: list[Path]) -> EvalResult:
        if not artifact_paths:
            return EvalResult(score=0.0, valid=False)
        total = 0
        passed = 0
        for p in artifact_paths:
            result = self.parse(p)
            if result.valid:
                total += 1
                if result.score > 0:
                    passed += 1
        if total == 0:
            return EvalResult(score=0.0, valid=False)
        score = passed / total
        return EvalResult(
            score=score,
            metrics={"passed": float(passed), "total": float(total), "pass_rate": score},
            valid=True,
        )

    def get_info(self) -> dict:
        return {
            "test_format": "exit_code",
            "scoring": "binary",
            "metrics": ["returncode", "passed"],
        }
