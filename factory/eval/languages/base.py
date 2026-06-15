"""Base types for the language evaluator protocol."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import structlog

log = structlog.get_logger()


@dataclass
class EvalFragment:
    """Result fragment from a single evaluator dimension."""

    passed: int
    failed: int
    score: float
    details: str
    coverage_pct: float | None = None

    def __post_init__(self) -> None:
        self.score = max(0.0, min(1.0, self.score))


@runtime_checkable
class LanguageEvaluator(Protocol):
    @property
    def name(self) -> str: ...

    def detect(self, project_path: Path) -> bool: ...

    def run_tests(self, project_path: Path, timeout: int = 300) -> EvalFragment | None: ...

    def run_lint(self, project_path: Path) -> EvalFragment | None: ...

    def run_type_check(self, project_path: Path) -> EvalFragment | None: ...

    def run_coverage(self, project_path: Path, timeout: int = 300) -> EvalFragment | None: ...

    def run_tests_with_coverage(
        self, project_path: Path, timeout: int = 300,
    ) -> tuple[EvalFragment | None, EvalFragment | None]: ...


def _run_cmd(
    cmd: list[str],
    cwd: Path,
    timeout: int = 300,
) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr). Never raises."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        if result.returncode != 0:
            log.debug(
                "subprocess_failed",
                cmd=cmd,
                cwd=str(cwd),
                returncode=result.returncode,
                stderr=result.stderr[:200] if result.stderr else "",
            )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"Timed out after {timeout}s"
    except FileNotFoundError:
        return 1, "", f"Command not found: {cmd[0]}"
    except Exception as exc:
        return 1, "", str(exc)
