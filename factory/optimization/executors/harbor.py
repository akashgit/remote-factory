"""HarborExecutor — runs run-harbor.sh with skill injection via env var.

Injects prompt skill content as base64-encoded env var, passes configuration
via constructor params, and parses per-task results from reward.json.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import structlog

from factory.optimization.surface import Surface
from factory.optimization.types import ExecutionResult, TaskResult

log = structlog.get_logger()


class HarborExecutor:
    """Executor that runs a Harbor benchmark via ``run-harbor.sh``.

    Injects skill content and configuration into the subprocess environment.
    """

    def __init__(
        self,
        harbor_script: str = "./run-harbor.sh",
        skill_env_var: str = "HARBOR_SKILL_PATH",
        docker_host: str | None = None,
        git_ref: str | None = None,
        n_tasks: int | None = None,
        concurrency: int | None = None,
        split: str | None = None,
    ) -> None:
        self.harbor_script = harbor_script
        self.skill_env_var = skill_env_var
        self.docker_host = docker_host
        self.git_ref = git_ref
        self.n_tasks = n_tasks
        self.concurrency = concurrency
        self.split = split

    def execute(
        self, project_dir: Path, surface: Surface, **kwargs: Any
    ) -> ExecutionResult:
        env = os.environ.copy()

        skill_path = kwargs.get("skill_path", "")
        if skill_path:
            env[self.skill_env_var] = str(skill_path)

        if "skill" in surface.prompt_slots:
            encoded = base64.b64encode(surface.prompt_slots["skill"].encode()).decode()
            env["SEARCHQA_SKILL_B64"] = encoded

        if self.docker_host:
            env["DOCKER_HOST"] = self.docker_host
        if self.git_ref:
            env["FACTORY_GIT_REF"] = self.git_ref
        if self.n_tasks is not None:
            env["FACTORY_N_TASKS"] = str(self.n_tasks)
        if self.concurrency is not None:
            env["FACTORY_CONCURRENCY"] = str(self.concurrency)
        if self.split:
            env["FACTORY_SPLIT"] = self.split

        script = project_dir / self.harbor_script
        if not script.exists():
            log.warning("executor.harbor.script_missing", path=str(script))
            return ExecutionResult(returncode=1, artifacts=[], duration_s=0.0)

        n_tasks = self.n_tasks if self.n_tasks is not None else 5
        concurrency = self.concurrency if self.concurrency is not None else 2

        cmd: list[str] = [
            str(script), "searchqa", "--all",
            "--limit", str(n_tasks),
            "--concurrency", str(concurrency),
            "--timeout", "120",
            "--solver", "factory",
            "--preserve",
        ]
        if self.split:
            cmd.extend(["--split", self.split])

        log.info("executor.harbor.start", script=str(script), n_tasks=n_tasks)
        start = time.monotonic()
        result = subprocess.run(cmd, cwd=project_dir, env=env)
        duration = time.monotonic() - start

        artifacts: list[str] = []
        task_results: list[TaskResult] = []

        jobs_dir = env.get("JOBS_DIR", "")
        if not jobs_dir:
            jobs_dir = _find_latest_jobs_dir()
        if jobs_dir:
            task_results = _parse_trial_results(Path(jobs_dir))
            if task_results:
                artifacts.append(jobs_dir)

        reward_path = project_dir / "reward.json"
        if reward_path.exists():
            artifacts.append(str(reward_path))
            if not task_results:
                task_results = _parse_task_results(reward_path)

        log.info(
            "executor.harbor.done",
            returncode=result.returncode,
            duration_s=round(duration, 1),
            tasks_parsed=len(task_results),
        )
        return ExecutionResult(
            returncode=result.returncode,
            artifacts=artifacts,
            duration_s=duration,
            task_results=task_results,
        )


def _find_latest_jobs_dir() -> str:
    """Find the most recently modified searchqa jobs directory in /tmp."""
    import glob as _glob

    candidates = _glob.glob("/tmp/searchqa-jobs-*")
    if not candidates:
        return ""
    candidates.sort(key=lambda p: Path(p).stat().st_mtime, reverse=True)
    return candidates[0]


def _strip_harbor_suffix(name: str) -> str:
    """Strip the ``__<7char>`` Harbor suffix from a trial directory name."""
    if len(name) > 9 and name[-8] == "_" and name[-9] == "_":
        return name[:-9]
    return name


def _parse_trial_results(jobs_dir: Path) -> list[TaskResult]:
    """Parse per-task verifier outputs from preserved trial directories."""
    if not jobs_dir.is_dir():
        return []

    results: list[TaskResult] = []
    for trial in sorted(jobs_dir.iterdir()):
        if not trial.is_dir():
            continue

        reward_file = trial / "verifier" / "reward.json"
        reward = 0.0
        if reward_file.exists():
            try:
                data = json.loads(reward_file.read_text())
                reward = float(data.get("reward", 0.0))
            except (json.JSONDecodeError, OSError, ValueError):
                pass

        predicted = ""
        gold = ""
        stdout_file = trial / "verifier" / "test-stdout.txt"
        if stdout_file.exists():
            try:
                for line in stdout_file.read_text().splitlines():
                    if line.startswith("Predicted:"):
                        predicted = line[len("Predicted:"):].strip()
                    elif line.startswith("Gold:"):
                        gold = line[len("Gold:"):].strip()
            except OSError:
                pass

        task_id = _strip_harbor_suffix(trial.name)
        results.append(
            TaskResult(
                task_id=task_id,
                reward=reward,
                predicted=predicted,
                gold=gold,
                question="",
            )
        )
    return results


def _parse_task_results(reward_path: Path) -> list[TaskResult]:
    """Parse per-task results from reward.json if 'tasks' key exists."""
    try:
        data = json.loads(reward_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return []

    results: list[TaskResult] = []
    for entry in tasks:
        if not isinstance(entry, dict):
            continue
        results.append(
            TaskResult(
                task_id=str(entry.get("task_id", "")),
                reward=float(entry.get("reward", 0.0)),
                predicted=str(entry.get("predicted", "")),
                gold=str(entry.get("gold", "")),
                question=str(entry.get("question", "")),
            )
        )
    return results
