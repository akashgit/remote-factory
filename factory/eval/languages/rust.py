"""Rust language evaluator."""

from __future__ import annotations

import re
from pathlib import Path

from factory.eval.languages.base import EvalFragment, _run_cmd


class RustEvaluator:
    @property
    def name(self) -> str:
        return "rust"

    def detect(self, project_path: Path) -> bool:
        return (project_path / "Cargo.toml").exists()

    def run_tests_with_coverage(
        self, project_path: Path,
    ) -> tuple[EvalFragment | None, EvalFragment | None]:
        rc, stdout, stderr = _run_cmd(
            ["cargo", "tarpaulin", "--out", "stdout", "--skip-clean"], project_path,
        )
        output = stdout + stderr

        test_frag: EvalFragment | None = None
        p_match = re.search(r"(\d+)\s+passed", output)
        f_match = re.search(r"(\d+)\s+failed", output)
        p = int(p_match.group(1)) if p_match else 0
        f = int(f_match.group(1)) if f_match else 0
        if p + f > 0:
            total = p + f
            test_frag = EvalFragment(
                passed=p,
                failed=f,
                score=p / total if total > 0 else 0.0,
                details=f"{project_path.name}(rs): {p} passed, {f} failed",
            )

        cov_frag: EvalFragment | None = None
        cov_match = re.search(r"([\d.]+)%\s+coverage", output)
        if cov_match:
            pct = float(cov_match.group(1))
            cov_frag = EvalFragment(
                passed=0,
                failed=0,
                score=pct / 100.0,
                coverage_pct=pct,
                details=f"{project_path.name}(rs): {pct:.0f}%",
            )

        return test_frag, cov_frag

    def run_tests(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(["cargo", "test"], project_path)
        output = stdout + stderr
        p_match = re.search(r"(\d+)\s+passed", output)
        f_match = re.search(r"(\d+)\s+failed", output)
        p = int(p_match.group(1)) if p_match else 0
        f = int(f_match.group(1)) if f_match else 0
        if p + f > 0:
            total = p + f
            return EvalFragment(
                passed=p,
                failed=f,
                score=p / total if total > 0 else 0.0,
                details=f"{project_path.name}(rs): {p} passed, {f} failed",
            )
        return None

    def run_lint(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(
            ["cargo", "clippy", "--", "-D", "warnings"], project_path
        )
        if rc == 0:
            return EvalFragment(
                passed=1, failed=0, score=1.0,
                details=f"{project_path.name}(rs): clean",
            )
        count = len(re.findall(r"^error", stderr, re.MULTILINE))
        count = max(count, 1)
        return EvalFragment(
            passed=0, failed=count, score=0.0,
            details=f"{project_path.name}(rs): {count} errors",
        )

    def run_type_check(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(
            ["cargo", "check"], project_path,
        )
        if rc == 0:
            return EvalFragment(
                passed=1, failed=0, score=1.0,
                details=f"{project_path.name}(rs): clean",
            )
        count = len(re.findall(r"^error", stderr, re.MULTILINE))
        count = max(count, 1)
        return EvalFragment(
            passed=0, failed=count, score=0.0,
            details=f"{project_path.name}(rs): {count} errors",
        )

    def run_coverage(self, project_path: Path) -> EvalFragment | None:
        _, cov_frag = self.run_tests_with_coverage(project_path)
        return cov_frag


def register_evaluator() -> RustEvaluator:
    return RustEvaluator()
