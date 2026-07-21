"""FeatureBench adapter — runs Harbor FeatureBench benchmarks and collects traces."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import structlog

from factory.skillopt.adapter import EnvAdapter
from factory.skillopt.types import RolloutResult

log = structlog.get_logger()

_BENCHMARKS_DIR = Path(__file__).resolve().parents[3] / "benchmarks"
_RESULTS_DIR = _BENCHMARKS_DIR / "results"
_SKILLS_DIR = Path(__file__).resolve().parents[3] / "skills" / "workflow-featurebench"

_JOBS_DIR_PATTERN = re.compile(r"Jobs directory:\s*(.+)")
_TRIAL_SUFFIX_PATTERN = re.compile(r"__[A-Za-z0-9]{7}$")


class FeaturebenchAdapter(EnvAdapter):

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
            str(script), "featurebench",
            "--all",
            "--timeout", "7200",
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
                timeout=9000,
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
        return ["feature_implementation"]


def _clean_result_files() -> None:
    """Remove stale *-featurebench-full.json files so the next run reads only fresh results."""
    if not _RESULTS_DIR.is_dir():
        return
    for f in _RESULTS_DIR.glob("*-featurebench-full.json"):
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
        _RESULTS_DIR.glob("*-featurebench-full.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _extract_trace_ids_from_jobs(jobs_dir: str) -> dict[str, str]:
    """Map instance_id → trace_id by scanning trace_id.txt files in JOBS_DIR."""
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


def _fetch_trace_dump(trace_id: str) -> str:
    """Fetch a trace from Langfuse and return a formatted dump string."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "langfuse"))
        from langfuse_client import fetch_trace  # type: ignore[import-untyped,import-not-found]

        trace: dict[str, Any] = fetch_trace(trace_id, use_cache=True)
        observations: list[dict[str, Any]] = trace.get("observations", [])
        parts = [f"Trace: {trace_id}"]
        parts.append(f"Name: {trace.get('name', 'unknown')}")
        parts.append(f"Latency: {trace.get('latency', 0):.0f}s")
        parts.append(f"Cost: ${trace.get('totalCost', 0):.4f}")
        parts.append(f"Observations: {len(observations)}")

        agent_spans = sorted(
            [o for o in observations
             if o.get("type") == "SPAN" and o.get("name", "").startswith("agent:")],
            key=lambda o: o.get("startTime", ""),
        )
        for span in agent_spans:
            inp = span.get("input", {})
            task_text = ""
            if isinstance(inp, dict):
                task_text = str(inp.get("task") or inp.get("prompt") or "")[:500]
            out = span.get("output", "")
            out_text = ""
            if isinstance(out, dict):
                out_text = json.dumps(out)[:500]
            elif out:
                out_text = str(out)[:500]
            parts.append(f"\n[{span.get('name', '')}] {span.get('startTime', '')[:19]}")
            if task_text:
                parts.append(f"  Input: {task_text}")
            if out_text:
                parts.append(f"  Output: {out_text}")

        return "\n".join(parts)
    except Exception as exc:
        log.warning("failed to fetch trace", trace_id=trace_id, error=str(exc))
        return ""


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
        trace_id = trace_map.get(instance_id, "")

        extras: dict[str, Any] = {}
        if trace_id:
            dump = _fetch_trace_dump(trace_id)
            if dump:
                extras["trace_dump"] = dump

        results.append(RolloutResult(
            id=instance_id,
            hard=1.0 if resolved else 0.0,
            soft=float(task.get("score", 1.0 if resolved else 0.0)),
            n_turns=int(task.get("n_turns", 0)),
            fail_reason=task.get("fail_reason", ""),
            task_type="feature_implementation",
            trace_id=trace_id,
            extras=extras,
        ))

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "rollout_results.json").write_text(
        json.dumps([r.model_dump() for r in results], indent=2)
    )
    log.info("collected results", count=len(results))
    return results
