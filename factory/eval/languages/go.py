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

    def run_tests_with_coverage(
        self, project_path: Path,
    ) -> tuple[EvalFragment | None, EvalFragment | None]:
        rc, stdout, stderr = _run_cmd(["go", "test", "-cover", "./..."], project_path)
        output = stdout + stderr

        test_frag: EvalFragment | None = None
        if rc == 0:
            ok_count = len(re.findall(r"^ok\s+", output, re.MULTILINE))
            test_frag = EvalFragment(
                passed=max(ok_count, 1),
                failed=0,
                score=1.0,
                details=f"{project_path.name}(go): passed",
            )
        elif "FAIL" in output:
            test_frag = EvalFragment(
                passed=0,
                failed=1,
                score=0.0,
                details=f"{project_path.name}(go): failed",
            )

        cov_frag: EvalFragment | None = None
        cov_matches = re.findall(r"coverage:\s+([\d.]+)%\s+of\s+statements", output)
        if cov_matches:
            pcts = [float(m) for m in cov_matches]
            avg_pct = sum(pcts) / len(pcts)
            cov_frag = EvalFragment(
                passed=0,
                failed=0,
                score=avg_pct / 100.0,
                coverage_pct=avg_pct,
                details=f"{project_path.name}(go): {avg_pct:.0f}%",
            )

        return test_frag, cov_frag

    def run_tests(self, project_path: Path) -> EvalFragment | None:
        test_frag, _ = self.run_tests_with_coverage(project_path)
        return test_frag

    def run_lint(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(["go", "vet", "./..."], project_path)
        output = stdout + stderr
        if rc == 0:
            return EvalFragment(
                passed=1, failed=0, score=1.0,
                details=f"{project_path.name}(go): clean",
            )
        count = len(re.findall(r"\w+\.go:\d+:\d+:", output))
        count = max(count, 1)
        return EvalFragment(
            passed=0, failed=count, score=0.0,
            details=f"{project_path.name}(go): {count} errors",
        )

    def run_type_check(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(
            ["go", "build", "-o", "/dev/null", "./..."], project_path,
        )
        output = stdout + stderr
        if rc == 0:
            return EvalFragment(
                passed=1, failed=0, score=1.0,
                details=f"{project_path.name}(go): clean",
            )
        count = len(re.findall(r"\w+\.go:\d+:\d+:", output))
        count = max(count, 1)
        return EvalFragment(
            passed=0, failed=count, score=0.0,
            details=f"{project_path.name}(go): {count} errors",
        )

    def run_coverage(self, project_path: Path) -> EvalFragment | None:
        _, cov_frag = self.run_tests_with_coverage(project_path)
        return cov_frag


def register_evaluator() -> GoEvaluator:
    return GoEvaluator()
