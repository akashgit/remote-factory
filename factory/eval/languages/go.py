"""Go language evaluator."""

from __future__ import annotations

import re
from pathlib import Path

from factory.eval.languages.base import EvalFragment, _run_cmd


class GoEvaluator:
    @property
    def name(self) -> str:
        return "go"

    def detect(self, project_path: Path) -> bool:
        return (project_path / "go.mod").exists()

    def run_tests(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(["go", "test", "./..."], project_path)
        output = stdout + stderr
        if rc == 0:
            ok_count = len(re.findall(r"^ok\s+", output, re.MULTILINE))
            return EvalFragment(
                passed=max(ok_count, 1),
                failed=0,
                score=1.0,
                details=f"{project_path.name}(go): passed",
            )
        if "FAIL" in output:
            return EvalFragment(
                passed=0,
                failed=1,
                score=0.0,
                details=f"{project_path.name}(go): failed",
            )
        return None

    def run_lint(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(["go", "vet", "./..."], project_path)
        if rc == 0:
            return EvalFragment(
                passed=1, failed=0, score=1.0,
                details=f"{project_path.name}(go): clean",
            )
        output = stdout + stderr
        count = len(output.strip().splitlines())
        count = max(count, 1)
        return EvalFragment(
            passed=0, failed=count, score=0.0,
            details=f"{project_path.name}(go): {count} errors",
        )

    def run_type_check(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(
            ["go", "build", "-o", "/dev/null", "./..."], project_path
        )
        if rc == 0:
            return EvalFragment(
                passed=1, failed=0, score=1.0,
                details=f"{project_path.name}(go): clean",
            )
        output = stdout + stderr
        count = len(re.findall(r"^.*\.go:\d+", output, re.MULTILINE))
        count = max(count, 1)
        return EvalFragment(
            passed=0, failed=count, score=0.0,
            details=f"{project_path.name}(go): {count} errors",
        )

    def run_coverage(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(["go", "test", "-cover", "./..."], project_path)
        output = stdout + stderr
        pcts = re.findall(r"coverage:\s+(\d+(?:\.\d+)?)%", output)
        if not pcts:
            return None
        avg_pct = sum(float(p) for p in pcts) / len(pcts)
        return EvalFragment(
            passed=int(avg_pct),
            failed=0,
            score=avg_pct / 100.0,
            details=f"{project_path.name}(go): {avg_pct:.0f}%",
        )


def register_evaluator() -> GoEvaluator:
    return GoEvaluator()
