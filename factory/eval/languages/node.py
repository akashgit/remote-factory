"""Node.js / TypeScript language evaluator."""

from __future__ import annotations

import re
from pathlib import Path

from factory.eval.languages.base import EvalFragment, _run_cmd


class NodeEvaluator:
    @property
    def name(self) -> str:
        return "typescript" if (self._project_path / "tsconfig.json").exists() else "javascript"

    def detect(self, project_path: Path) -> bool:
        if not (project_path / "package.json").exists():
            return False
        self._project_path = project_path
        return True

    def run_tests(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(
            ["npm", "test", "--", "--passWithNoTests"], project_path, timeout=180
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
            details=f"{project_path.name}(js): {p} passed, {f} failed",
        )

    def run_lint(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(
            ["npx", "eslint", ".", "--format=compact"], project_path, timeout=180
        )
        output = stdout + stderr
        if rc == 0:
            return EvalFragment(
                passed=1, failed=0, score=1.0,
                details=f"{project_path.name}(js): clean",
            )
        count = len(re.findall(r"Error -", output))
        count = max(count, 1)
        return EvalFragment(
            passed=0, failed=count, score=0.0,
            details=f"{project_path.name}(js): {count} errors",
        )

    def run_type_check(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(
            ["npx", "tsc", "--noEmit"], project_path, timeout=180
        )
        output = stdout + stderr
        if rc == 0:
            return EvalFragment(
                passed=1, failed=0, score=1.0,
                details=f"{project_path.name}(ts): clean",
            )
        count = len(re.findall(r"error TS\d+", output))
        count = max(count, 1)
        return EvalFragment(
            passed=0, failed=count, score=0.0,
            details=f"{project_path.name}(ts): {count} errors",
        )

    def run_coverage(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(
            ["npx", "--no-install", "jest", "--coverage", "--coverageReporters=text",
             "--passWithNoTests"],
            project_path, timeout=180,
        )
        output = stdout + stderr
        total_match = re.search(r"All files\s*\|\s*(\d+(?:\.\d+)?)", output)
        if not total_match:
            return None
        pct = float(total_match.group(1))
        return EvalFragment(
            passed=int(pct),
            failed=0,
            score=pct / 100.0,
            details=f"{project_path.name}(js): {pct:.0f}%",
        )


def register_evaluator() -> NodeEvaluator:
    return NodeEvaluator()
