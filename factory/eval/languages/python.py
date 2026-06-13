"""Python language evaluator."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from factory.eval.languages.base import EvalFragment, _run_cmd


class PythonEvaluator:
    @property
    def name(self) -> str:
        return "python"

    def detect(self, project_path: Path) -> bool:
        return (
            (project_path / "pyproject.toml").exists()
            or (project_path / "setup.py").exists()
        )

    def run_tests(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(
            [sys.executable, "-m", "pytest", "-v", "--tb=no", "-q"], project_path
        )
        output = stdout + stderr
        p_match = re.search(r"(\d+)\s+passed", output)
        f_match = re.search(r"(\d+)\s+failed", output)
        p = int(p_match.group(1)) if p_match else 0
        f = int(f_match.group(1)) if f_match else 0
        if p + f == 0:
            return None
        total = p + f
        return EvalFragment(
            passed=p,
            failed=f,
            score=p / total if total > 0 else 0.0,
            details=f"{project_path.name}: {p} passed, {f} failed",
        )

    def run_lint(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(
            [sys.executable, "-m", "ruff", "check", "."], project_path
        )
        output = stdout + stderr
        if rc == 0:
            return EvalFragment(passed=1, failed=0, score=1.0, details=f"{project_path.name}: clean")
        err_match = re.search(r"Found\s+(\d+)\s+error", output)
        count = int(err_match.group(1)) if err_match else 1
        return EvalFragment(passed=0, failed=count, score=0.0, details=f"{project_path.name}: {count} errors")

    def run_type_check(self, project_path: Path) -> EvalFragment | None:
        src_dirs = []
        for child in sorted(project_path.iterdir()):
            if child.is_dir() and (child / "__init__.py").exists():
                src_dirs.append(child.name)
        target = src_dirs[0] if src_dirs else "."
        rc, stdout, stderr = _run_cmd(
            [sys.executable, "-m", "mypy", target], project_path
        )
        output = stdout + stderr
        if rc == 0:
            return EvalFragment(passed=1, failed=0, score=1.0, details=f"{project_path.name}: clean")
        err_match = re.search(r"Found\s+(\d+)\s+error", output)
        count = int(err_match.group(1)) if err_match else 1
        return EvalFragment(
            passed=0, failed=count, score=0.0,
            details=f"{project_path.name}: {count} errors",
        )

    def run_coverage(self, project_path: Path) -> EvalFragment | None:
        src_dirs = [
            c.name for c in sorted(project_path.iterdir())
            if c.is_dir() and (c / "__init__.py").exists()
        ]
        cov_target = src_dirs[0] if src_dirs else "."
        rc, stdout, stderr = _run_cmd(
            [sys.executable, "-m", "pytest", f"--cov={cov_target}", "--cov-report=term", "-q"],
            project_path,
        )
        output = stdout + stderr
        total_match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", output)
        if not total_match:
            return None
        pct = int(total_match.group(1))
        return EvalFragment(
            passed=0,
            failed=0,
            score=pct / 100.0,
            coverage_pct=pct,
            details=f"{project_path.name}: {pct}%",
        )


def register_evaluator() -> PythonEvaluator:
    return PythonEvaluator()
