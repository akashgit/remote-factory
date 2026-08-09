"""FactoryCeoExecutor — runs factory ceo as a subprocess."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import structlog

from factory.optimization.surface import Surface
from factory.optimization.types import ExecutionResult

log = structlog.get_logger()


class FactoryCeoExecutor:
    """Executor that runs ``factory ceo`` as a blocking subprocess.

    Absorbs the subprocess call pattern from InnerLoop.step().
    """

    def __init__(self, mode: str = "improve", extra_args: list[str] | None = None) -> None:
        self.mode = mode
        self.extra_args = extra_args or []

    def execute(
        self, project_dir: Path, surface: Surface, **kwargs: Any
    ) -> ExecutionResult:
        cmd = [
            sys.executable, "-m", "factory", "ceo", str(project_dir),
            "--mode", self.mode, "--no-worktree",
            *self.extra_args,
        ]
        log.info("executor.ceo.start", project=str(project_dir), mode=self.mode)
        start = time.monotonic()
        result = subprocess.run(cmd, cwd=project_dir)
        duration = time.monotonic() - start
        log.info("executor.ceo.done", returncode=result.returncode, duration_s=round(duration, 1))
        return ExecutionResult(
            returncode=result.returncode,
            duration_s=duration,
        )
