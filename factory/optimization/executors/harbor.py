"""HarborExecutor — runs run-harbor.sh with skill injection via env var.

Stub implementation: Harbor infrastructure may not be on this branch.
The full implementation will inject the skill path into the subprocess
environment and collect result artifacts from the Harbor output directory.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import structlog

from factory.optimization.surface import Surface
from factory.optimization.types import ExecutionResult

log = structlog.get_logger()


class HarborExecutor:
    """Executor that runs a Harbor benchmark via ``run-harbor.sh``.

    Injects the skill path into the subprocess environment so the Harbor
    runner picks up the current prompt surface.
    """

    def __init__(
        self,
        harbor_script: str = "./run-harbor.sh",
        skill_env_var: str = "HARBOR_SKILL_PATH",
    ) -> None:
        self.harbor_script = harbor_script
        self.skill_env_var = skill_env_var

    def execute(
        self, project_dir: Path, surface: Surface, **kwargs: Any
    ) -> ExecutionResult:
        import os

        env = os.environ.copy()
        skill_path = kwargs.get("skill_path", "")
        if skill_path:
            env[self.skill_env_var] = str(skill_path)

        script = project_dir / self.harbor_script
        if not script.exists():
            log.warning("executor.harbor.script_missing", path=str(script))
            return ExecutionResult(returncode=1, artifacts=[], duration_s=0.0)

        log.info("executor.harbor.start", script=str(script))
        start = time.monotonic()
        result = subprocess.run(
            [str(script)],
            cwd=project_dir,
            env=env,
        )
        duration = time.monotonic() - start

        artifacts: list[str] = []
        reward_path = project_dir / "reward.json"
        if reward_path.exists():
            artifacts.append(str(reward_path))

        log.info("executor.harbor.done", returncode=result.returncode, duration_s=round(duration, 1))
        return ExecutionResult(
            returncode=result.returncode,
            artifacts=artifacts,
            duration_s=duration,
        )
