"""SearchQA adapter — runs Harbor SearchQA benchmarks and collects EM/F1 scores."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import structlog

from factory.skillopt.adapter import EnvAdapter
from factory.skillopt.types import RolloutResult

log = structlog.get_logger()

_BENCHMARKS_DIR = Path(__file__).resolve().parents[3] / "benchmarks"
_RESULTS_DIR = _BENCHMARKS_DIR / "results"
_SKILLS_DIR = Path(__file__).resolve().parents[3] / "skills" / "workflow-searchqa"

_JOBS_DIR_PATTERN = re.compile(r"Jobs directory:\s*(.+)")
_TRIAL_SUFFIX_PATTERN = re.compile(r"__[A-Za-z0-9]{7}$")


class SearchQAAdapter(EnvAdapter):

    def __init__(self) -> None:
        self.skill_path: Path = _SKILLS_DIR / "SKILL.md"
        self.instances: list[str] = []

    def setup(self, cfg: dict) -> None:
        self.skill_path = Path(cfg.get("skill_path", str(self.skill_path)))
        self.instances = cfg.get("instances", [])

    def build_train_env(self, batch_size: int, seed: int) -> Any:
        if self.instances:
            log.info("train env built (pinned instances)", count=len(self.instances), seed=seed)
            return self.instances
        log.info("train env built", limit=batch_size, seed=seed)
        return batch_size

    def build_eval_env(self, env_num: int, split: str, seed: int) -> Any:
        if self.instances:
            log.info("eval env built (pinned instances)", count=len(self.instances), split=split, seed=seed)
            return self.instances
        log.info("eval env built", limit=env_num, split=split, seed=seed)
        return env_num

    def rollout(
        self, env_manager: Any, skill_content: str, out_dir: str,
    ) -> list[RolloutResult]:
        self.skill_path.parent.mkdir(parents=True, exist_ok=True)
        self.skill_path.write_text(skill_content)
        log.info("skill written", path=str(self.skill_path))

        script = _BENCHMARKS_DIR / "run-harbor.sh"
        if not script.exists():
            log.error("run-harbor.sh not found", path=str(script))
            return []

        _clean_result_files()

        cmd = [
            str(script), "searchqa",
            "--all",
            "--timeout", "3600",
            "--preserve",
        ]
        if self.instances:
            for instance_id in self.instances:
                cmd += ["--include-task-name", instance_id]
        else:
            limit = int(env_manager) if env_manager else 0
            if limit > 0:
                cmd += ["--limit", str(limit)]

        log.info("running harbor", cmd=" ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5400,
            )
            log.info("benchmark finished", returncode=result.returncode)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            log.error("benchmark failed", error=str(exc))
            return []

        jobs_dir = _parse_jobs_dir(result.stdout)
        if jobs_dir:
            log.info("jobs dir found", path=jobs_dir)

        results = _collect_results(out_dir, jobs_dir)
        if not results:
            log.error(
                "rollout produced no results — possible Harbor dedup or task mismatch",
                instances=self.instances,
                returncode=result.returncode,
                stderr_tail=result.stderr[-500:] if result.stderr else "",
            )
        return results

    def get_task_types(self) -> list[str]:
        return ["question_answering"]


def _clean_result_files() -> None:
    if not _RESULTS_DIR.is_dir():
        return
    for f in _RESULTS_DIR.glob("*-searchqa-full.json"):
        try:
            f.unlink()
            log.info("removed stale result file", path=str(f))
        except OSError:
            pass


def _parse_jobs_dir(stdout: str) -> str:
    for line in stdout.splitlines():
        m = _JOBS_DIR_PATTERN.search(line)
        if m:
            return m.group(1).strip()
    return ""


def _find_latest_result_file() -> Path | None:
    if not _RESULTS_DIR.is_dir():
        return None
    candidates = sorted(
        _RESULTS_DIR.glob("*-searchqa-full.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _extract_trace_ids_from_jobs(jobs_dir: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not jobs_dir:
        return mapping
    jobs_path = Path(jobs_dir)
    if not jobs_path.is_dir():
        return mapping

    for trace_file in jobs_path.rglob("trace_id.txt"):
        trace_id = trace_file.read_text().strip()
        if not trace_id:
            continue
        trial_dir = trace_file.parent
        if trial_dir.name in ("verifier", "agent"):
            trial_dir = trial_dir.parent
        instance_id = _TRIAL_SUFFIX_PATTERN.sub("", trial_dir.name)
        if instance_id:
            mapping[instance_id] = trace_id

    return mapping


def _collect_results(out_dir: str, jobs_dir: str) -> list[RolloutResult]:
    result_file = _find_latest_result_file()
    if not result_file:
        log.warning("no result file found in benchmarks/results/")
        return []

    try:
        data = json.loads(result_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.error("failed to parse result file", path=str(result_file), error=str(exc))
        return []

    tasks = data.get("tasks", [])
    if not tasks:
        log.warning("no tasks in result file", path=str(result_file))
        return []

    trace_map = _extract_trace_ids_from_jobs(jobs_dir)
    log.info("trace ids extracted", count=len(trace_map))

    results: list[RolloutResult] = []
    for task in tasks:
        instance_id = task.get("instance_id", "")
        resolved = task.get("resolved", False)
        em = float(task.get("exact_match", 1.0 if resolved else 0.0))
        f1 = float(task.get("f1", task.get("score", 1.0 if resolved else 0.0)))
        trace_id = trace_map.get(instance_id, "")

        results.append(RolloutResult(
            id=instance_id,
            hard=em,
            soft=f1,
            n_turns=int(task.get("n_turns", 0)),
            fail_reason=task.get("fail_reason", ""),
            task_type="question_answering",
            trace_id=trace_id,
        ))

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "rollout_results.json").write_text(
        json.dumps([r.model_dump() for r in results], indent=2)
    )
    log.info("collected results", count=len(results))
    return results
