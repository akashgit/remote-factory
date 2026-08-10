"""HarborBenchmark — runs ``uvx harbor run`` directly with proper args.

Production executor for the optimization loop. Builds the full ``uvx harbor run``
command with skill injection via ``--ae``, auth env propagation, GCP credential
mounts, and per-invocation jobs directories. Parses per-task results from
``<jobs-dir>/*/verifier/reward.json``.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import structlog

from factory.optimization.executors.harbor import _parse_trial_results
from factory.optimization.surface import Surface
from factory.optimization.types import ExecutionResult, TaskResult

log = structlog.get_logger()

_AUTH_ENV_KEYS = (
    "CLAUDE_CODE_USE_VERTEX",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "CLOUD_ML_REGION",
    "CLOUD_ML_PROJECT_ID",
    "GOOGLE_APPLICATION_CREDENTIALS",
)


class HarborBenchmark:
    """Executor that calls ``uvx harbor run`` directly with structured args.

    Unlike the legacy ``HarborExecutor`` (which wraps a shell script), this
    class constructs the full CLI invocation, injects auth and skill via
    ``--ae`` flags, mounts GCP credentials, and parses per-task results from
    the jobs directory.
    """

    def __init__(
        self,
        git_ref: str = "main",
        subset_dir: str | Path | None = None,
        concurrency: int = 5,
        docker_host: str | None = None,
        model: str = "sonnet",
        auth_env: dict[str, str] | None = None,
        cleanup_jobs: bool = True,
    ) -> None:
        self.git_ref = git_ref
        self.subset_dir = Path(subset_dir) if subset_dir else None
        self.concurrency = concurrency
        self.docker_host = docker_host or os.environ.get("DOCKER_HOST", "")
        self.model = model
        self.auth_env = auth_env or {}
        self.cleanup_jobs = cleanup_jobs
        self._run = 0

    def execute(
        self, project_dir: Path, surface: Surface, **kwargs: Any
    ) -> ExecutionResult:
        self._run += 1
        start = time.monotonic()

        env = os.environ.copy()
        if self.docker_host:
            env["DOCKER_HOST"] = self.docker_host
        env["FACTORY_GIT_REF"] = self.git_ref

        skill = surface.prompt_slots.get("skill", "")
        skill_b64 = ""
        if skill:
            skill_b64 = base64.b64encode(skill.encode()).decode()

        jobs_dir = Path(tempfile.mkdtemp(prefix=f"optimize-jobs-{self._run}-"))
        task_dir = str(self.subset_dir) if self.subset_dir else str(project_dir)

        model_str = self.model
        if "/" not in model_str:
            model_str = f"anthropic/claude-{model_str}"

        cmd: list[str] = [
            "uvx", "harbor", "run",
            "--model", model_str,
            "-p", task_dir,
            "--agent", "factory_harbor_agent:SearchQAFactoryCeo",
            "--n-concurrent", str(self.concurrency),
            "--timeout-multiplier", "1",
            "--jobs-dir", str(jobs_dir),
        ]

        ae_flags: list[str] = []

        ae_flags += ["--ae", f"FACTORY_GIT_REF={self.git_ref}"]
        if skill_b64:
            ae_flags += ["--ae", f"SEARCHQA_SKILL_B64={skill_b64}"]

        resolved_auth = dict(self.auth_env)
        for key in _AUTH_ENV_KEYS:
            if key not in resolved_auth and os.environ.get(key):
                resolved_auth[key] = os.environ[key]

        if resolved_auth.get("ANTHROPIC_VERTEX_PROJECT_ID"):
            ae_flags += ["--ae", "CLAUDE_CODE_USE_VERTEX=1"]
            ae_flags += ["--ae", f"ANTHROPIC_VERTEX_PROJECT_ID={resolved_auth['ANTHROPIC_VERTEX_PROJECT_ID']}"]
            region = resolved_auth.get("CLOUD_ML_REGION", os.environ.get("CLOUD_ML_REGION", "global"))
            ae_flags += ["--ae", f"CLOUD_ML_REGION={region}"]
            ae_flags += ["--ae", "GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcloud-adc.json"]

        ae_flags += ["--ae", f"ANTHROPIC_MODEL={os.environ.get('ANTHROPIC_MODEL', 'claude-opus-4-6')}"]

        gcp_creds = resolved_auth.get("GOOGLE_APPLICATION_CREDENTIALS", "")
        if not gcp_creds:
            gcp_creds_path = Path.home() / ".config/gcloud/application_default_credentials.json"
            if gcp_creds_path.exists():
                gcp_creds = str(gcp_creds_path)

        if gcp_creds and Path(gcp_creds).exists():
            mount = [{"type": "bind", "source": gcp_creds, "target": "/tmp/gcloud-adc.json", "read_only": True}]
            cmd += ["--mounts", json.dumps(mount)]

        cmd += ae_flags

        benchmarks_dir = project_dir / "benchmarks"
        pythonpath = str(benchmarks_dir) + ":" + env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = pythonpath

        log.info("harbor_benchmark.start", step=self._run, concurrency=self.concurrency, jobs_dir=str(jobs_dir))
        result = subprocess.run(cmd, cwd=str(project_dir), env=env, capture_output=True, text=True)
        duration = time.monotonic() - start

        if result.returncode != 0:
            log.warning("harbor_benchmark.failed", returncode=result.returncode, stderr_tail=result.stderr[-300:] if result.stderr else "")

        task_results: list[TaskResult] = []
        artifacts: list[str] = []

        if jobs_dir.exists():
            task_results = _parse_trial_results(jobs_dir)

        n_correct = sum(1 for t in task_results if t.reward > 0)
        n_total = len(task_results)
        acc = n_correct / n_total if n_total else 0.0

        agg_file = Path(tempfile.mkdtemp(prefix="harbor-agg-")) / "results.json"
        agg_file.write_text(json.dumps({"accuracy": acc}))
        artifacts.append(str(agg_file))

        log.info(
            "harbor_benchmark.done",
            step=self._run,
            correct=n_correct,
            total=n_total,
            accuracy=round(acc, 4),
            duration_s=round(duration, 1),
        )

        if self.cleanup_jobs and jobs_dir.exists():
            shutil.rmtree(jobs_dir, ignore_errors=True)

        return ExecutionResult(
            returncode=result.returncode,
            artifacts=artifacts,
            duration_s=duration,
            task_results=task_results,
        )
