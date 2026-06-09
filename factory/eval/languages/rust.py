"""Rust language evaluator."""

from __future__ import annotations

import os
import re
from pathlib import Path

from factory.eval.languages.base import EvalFragment, _run_cmd


class RustEvaluator:
    @property
    def name(self) -> str:
        return "rust"

    def detect(self, project_path: Path) -> bool:
        return (project_path / "Cargo.toml").exists()

    def _rust_env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        cargo_bin = str(Path.home() / ".cargo" / "bin")
        path = env.get("PATH", "")
        if cargo_bin not in path:
            env["PATH"] = cargo_bin + os.pathsep + path
        return env

    def run_tests(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(
            ["cargo", "test", "--workspace"], project_path
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
            details=f"{project_path.name}(rs): {p} passed, {f} failed",
        )

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
        rc, stdout, stderr = _run_cmd(["cargo", "check"], project_path)
        if rc == 0:
            return EvalFragment(
                passed=1, failed=0, score=1.0,
                details=f"{project_path.name}(rs): clean",
            )
        output = stdout + stderr
        count = len(re.findall(r"^error", output, re.MULTILINE))
        count = max(count, 1)
        return EvalFragment(
            passed=0, failed=count, score=0.0,
            details=f"{project_path.name}(rs): {count} errors",
        )

    def run_coverage(self, project_path: Path) -> EvalFragment | None:
        rc, stdout, stderr = _run_cmd(
            ["cargo", "tarpaulin", "--out", "stdout", "--skip-clean"], project_path
        )
        output = stdout + stderr
        total_match = re.search(r"(\d+(?:\.\d+)?)%\s+coverage", output)
        if not total_match:
            return None
        pct = float(total_match.group(1))
        return EvalFragment(
            passed=int(pct),
            failed=0,
            score=pct / 100.0,
            details=f"{project_path.name}(rs): {pct:.0f}%",
        )


def register_evaluator() -> RustEvaluator:
    return RustEvaluator()
