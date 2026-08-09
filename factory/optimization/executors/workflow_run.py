"""WorkflowRunExecutor — wraps ``factory workflow run`` for deterministic headless execution."""

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


class WorkflowRunExecutor:
    """Executor that runs ``factory workflow run <name>`` as a subprocess."""

    def __init__(self, workflow_name: str) -> None:
        self.workflow_name = workflow_name

    def execute(
        self, project_dir: Path, surface: Surface, **kwargs: Any
    ) -> ExecutionResult:
        cmd = [
            sys.executable, "-m", "factory", "workflow", "run",
            self.workflow_name, str(project_dir),
        ]
        log.info("executor.workflow_run.start", workflow=self.workflow_name)
        start = time.monotonic()
        result = subprocess.run(cmd, cwd=project_dir)
        duration = time.monotonic() - start
        log.info(
            "executor.workflow_run.done",
            returncode=result.returncode,
            duration_s=round(duration, 1),
        )
        return ExecutionResult(
            returncode=result.returncode,
            duration_s=duration,
        )
