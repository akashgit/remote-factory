"""SWE-bench adapter — runs Harbor SWE-bench benchmarks and collects traces."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import structlog

from factory.skillopt.adapter import EnvAdapter
from factory.skillopt.types import RolloutResult

log = structlog.get_logger()

_BENCHMARKS_DIR = Path(__file__).resolve().parents[3] / "benchmarks"
_SKILLS_DIR = Path(__file__).resolve().parents[3] / "skills" / "workflow-swebench"


class SwebenchAdapter(EnvAdapter):

    def __init__(self) -> None:
        self.instances: list[str] = []
        self.results_dir: str = ""
        self.skill_path: Path = _SKILLS_DIR / "SKILL.md"

    def setup(self, cfg: dict) -> None:
        self.results_dir = cfg.get("results_dir", "")
        instances_file = cfg.get("instances_file", "")
        if instances_file and Path(instances_file).exists():
            self.instances = json.loads(Path(instances_file).read_text())
        self.skill_path = Path(cfg.get("skill_path", str(self.skill_path)))

    def build_train_env(self, batch_size: int, seed: int) -> Any:
        if not self.instances:
            log.warning("no instances configured, returning empty train env")
            return []
        import random
        rng = random.Random(seed)
        selected = rng.sample(self.instances, min(batch_size, len(self.instances)))
        log.info("train env built", instances=len(selected), seed=seed)
        return selected

    def build_eval_env(self, env_num: int, split: str, seed: int) -> Any:
        if not self.instances:
            return []
        import random
        rng = random.Random(seed)
        shuffled = list(self.instances)
        rng.shuffle(shuffled)
        split_point = len(shuffled) // 2
        if split == "eval":
            selected = shuffled[split_point:]
        else:
            selected = shuffled[:split_point]
        return selected[:env_num]

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

        try:
            result = subprocess.run(
                [str(script), "swebench"],
                capture_output=True,
                text=True,
                timeout=7200,
            )
            log.info("benchmark finished", returncode=result.returncode)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            log.error("benchmark failed", error=str(exc))
            return []

        return self._collect_results(out_dir)

    def _collect_results(self, out_dir: str) -> list[RolloutResult]:
        results_path = Path(self.results_dir) if self.results_dir else None
        if not results_path or not results_path.is_dir():
            log.warning("results dir not found", path=self.results_dir)
            return []

        results: list[RolloutResult] = []
        for rf in sorted(results_path.glob("*.json")):
            try:
                data = json.loads(rf.read_text())
            except (json.JSONDecodeError, OSError):
                continue

            instance_id = data.get("instance_id", rf.stem)
            resolved = data.get("resolved", False)
            details = data.get("details", {}) or {}
            trace_id = details.get("trace_id", "")

            extras: dict = {}
            trace_dump = details.get("trace_dump", "")
            if trace_dump:
                extras["trace_dump"] = trace_dump

            results.append(RolloutResult(
                id=instance_id,
                hard=1.0 if resolved else 0.0,
                soft=float(data.get("score", 1.0 if resolved else 0.0)),
                n_turns=int(details.get("n_turns", 0)),
                fail_reason=data.get("fail_reason", "") or details.get("error", ""),
                task_type="bug_fix",
                trace_id=trace_id,
                extras=extras,
            ))

        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "rollout_results.json").write_text(
            json.dumps([r.model_dump() for r in results], indent=2)
        )
        log.info("collected results", count=len(results))
        return results

    def get_task_types(self) -> list[str]:
        return ["bug_fix"]
