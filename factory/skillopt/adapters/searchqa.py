"""SearchQA adapter — runs Harbor SearchQA benchmarks and collects results."""
from __future__ import annotations

import base64
import json
import os
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
_DATA_DIR = _BENCHMARKS_DIR / "searchqa-harbor"

_JOBS_DIR_PATTERN = re.compile(r"Jobs directory:\s*(.+)")
_TRIAL_SUFFIX_PATTERN = re.compile(r"__[A-Za-z0-9]{7}$")


class SearchQAAdapter(EnvAdapter):

    def __init__(self) -> None:
        self.skill_path: Path = _SKILLS_DIR / "SKILL.md"
        self.data_dir: Path = _DATA_DIR
        self.instances: list[str] = []

    def setup(self, cfg: dict) -> None:
        self.skill_path = Path(cfg.get("skill_path", str(self.skill_path)))
        if cfg.get("dataset_dir"):
            self.data_dir = Path(cfg["dataset_dir"])
        self.instances = cfg.get("instances", [])

    def _list_task_ids(self, split: str) -> list[str]:
        split_dir = self.data_dir / split
        if not split_dir.is_dir():
            log.warning("split directory not found", path=str(split_dir))
            return []
        return sorted(d.name for d in split_dir.iterdir() if d.is_dir())

    def build_train_env(self, batch_size: int, seed: int) -> Any:
        if self.instances:
            log.info("train env built (pinned instances)", count=len(self.instances), seed=seed)
            return self.instances
        log.info("train env built", limit=batch_size, seed=seed)
        return batch_size

    def build_eval_env(self, env_num: int, split: str, seed: int) -> Any:
        if self.instances:
            log.info("eval env built (pinned instances)", count=len(self.instances), split=split, seed=seed)
            return ("val", self.instances)
        log.info("eval env built", limit=env_num, split=split, seed=seed)
        return ("val", env_num)

    def _extract_prompt_slot(self, skill_content: str) -> str:
        """Extract just the prompt text from SKILL.md or return as-is if already raw."""
        if not skill_content.startswith("---"):
            return skill_content
        from factory.skillopt.yaml_surface import load_yaml, extract_prompt_slots
        ann_path = self.skill_path.parent / "SKILL.annotations.yaml"
        if ann_path.exists():
            surface = load_yaml(ann_path)
            slots = extract_prompt_slots(surface)
            if slots:
                return next(iter(slots.values()))
        import re
        match = re.search(
            r'factory agent builder --task "(.*?)"\s*--project',
            skill_content, re.DOTALL,
        )
        if match:
            return match.group(1).strip()
        return skill_content

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

        split = "train"
        limit = 0
        instances: list[str] = []
        if isinstance(env_manager, tuple):
            split, payload = env_manager
            if isinstance(payload, list):
                instances = payload
            else:
                limit = int(payload) if payload else 0
        elif isinstance(env_manager, list):
            instances = env_manager
        else:
            limit = int(env_manager) if env_manager else 0

        cmd = [
            str(script), "searchqa",
            "--all",
            "--timeout", "3600",
            "--preserve",
        ]
        if instances:
            for instance_id in instances:
                cmd += ["--include-task-name", instance_id]
        elif limit > 0:
            cmd += ["--limit", str(limit)]

        env = dict(os.environ)
        env["SEARCHQA_SPLIT"] = split
        prompt = self._extract_prompt_slot(skill_content)
        env["FACTORY_SKILL_B64"] = base64.b64encode(
            prompt.encode()
        ).decode()

        git_ref = _get_git_ref()
        if git_ref:
            env["FACTORY_GIT_REF"] = git_ref

        log.info("running harbor", cmd=" ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=9000,
                env=env,
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


def _get_git_ref() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _clean_result_files() -> None:
    if not _RESULTS_DIR.is_dir():
        return
    for f in _RESULTS_DIR.glob("*-searchqa-*.json"):
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
        _RESULTS_DIR.glob("*-searchqa-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _extract_verifier_outputs(jobs_dir: str) -> dict[str, dict]:
    """Read verifier test-stdout.txt from Harbor jobs to get predicted/gold answers."""
    outputs: dict[str, dict] = {}
    if not jobs_dir:
        return outputs
    jobs_path = Path(jobs_dir)
    for stdout_file in jobs_path.rglob("verifier/test-stdout.txt"):
        trial_dir = stdout_file.parent.parent
        instance_id = _TRIAL_SUFFIX_PATTERN.sub("", trial_dir.name)
        if not instance_id:
            continue
        text = stdout_file.read_text()
        predicted = ""
        gold: list[str] = []
        for line in text.splitlines():
            if line.startswith("Predicted: "):
                predicted = line[len("Predicted: "):]
            elif line.startswith("Gold: "):
                try:
                    gold = json.loads(line[len("Gold: "):].replace("'", '"'))
                except (json.JSONDecodeError, ValueError):
                    gold = [line[len("Gold: "):]]
        outputs[instance_id] = {"predicted": predicted, "gold": gold}
    return outputs


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

    verifier_outputs = _extract_verifier_outputs(jobs_dir) if jobs_dir else {}

    results: list[RolloutResult] = []
    for task in tasks:
        instance_id = task.get("instance_id", "")
        resolved = task.get("resolved", False)
        reward = 1.0 if resolved else 0.0

        verifier = verifier_outputs.get(instance_id, {})
        predicted = verifier.get("predicted", "")
        gold = verifier.get("gold", [])

        fail_reason = ""
        if not resolved and predicted:
            fail_reason = f"EM=0: predicted '{predicted}' but expected {gold}"
        elif not resolved:
            fail_reason = "not_resolved"

        results.append(RolloutResult(
            id=instance_id,
            hard=reward,
            soft=reward,
            n_turns=0,
            fail_reason=fail_reason,
            task_type="question_answering",
            extras={
                "prediction": predicted,
                "gold_answers": gold,
                "trace_dump": (
                    f"[EVALUATION RESULT]\n"
                    f"Predicted answer: {predicted!r}\n"
                    f"Gold answers: {gold!r}\n"
                    f"Exact Match: {reward}\n"
                ) if predicted else "",
            },
        ))

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "rollout_results.json").write_text(
        json.dumps([r.model_dump() for r in results], indent=2)
    )
    log.info("collected results", count=len(results))
    return results
