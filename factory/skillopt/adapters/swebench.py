"""SWE-bench adapter — runs Harbor SWE-bench benchmarks and collects traces."""
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
_SKILLS_DIR = Path(__file__).resolve().parents[3] / "skills" / "workflow-swebench"
_SPLITS_DIR = _BENCHMARKS_DIR / "swebench-subset" / "splits"

_JOBS_DIR_PATTERN = re.compile(r"Jobs directory:\s*(.+)")
_TRIAL_SUFFIX_PATTERN = re.compile(r"__[A-Za-z0-9]{7}$")


def _load_split_ids(split_file: Path) -> list[str]:
    if not split_file.exists():
        return []
    ids: list[str] = []
    for line in split_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict) and "instance_id" in data:
                ids.append(data["instance_id"])
        except json.JSONDecodeError:
            continue
    return ids


class SwebenchAdapter(EnvAdapter):

    def __init__(self) -> None:
        self.skill_path: Path = _SKILLS_DIR / "SKILL.md"
        self.instances: list[str] = []
        self.student_model: str = ""
        self._train_ids: list[str] = []
        self._val_ids: list[str] = []
        self._test_ids: list[str] = []

    def setup(self, cfg: dict) -> None:
        self.skill_path = Path(cfg.get("skill_path", str(self.skill_path)))
        self.instances = cfg.get("instances", [])
        self.student_model = cfg.get("student_model", "")
        self._train_ids = _load_split_ids(_SPLITS_DIR / "train.jsonl")
        self._val_ids = _load_split_ids(_SPLITS_DIR / "val.jsonl")
        self._test_ids = _load_split_ids(_SPLITS_DIR / "test.jsonl")
        log.info(
            "splits loaded",
            train=len(self._train_ids),
            val=len(self._val_ids),
            test=len(self._test_ids),
        )

    def build_train_env(self, batch_size: int, seed: int) -> Any:
        if self.instances:
            log.info("train env built (pinned instances)", count=len(self.instances), seed=seed)
            return self.instances
        if self._train_ids:
            start = (seed * batch_size) % max(len(self._train_ids), 1)
            selected = self._train_ids[start:start + batch_size]
            log.info("train env built (split)", count=len(selected), seed=seed)
            return selected
        log.info("train env built", limit=batch_size, seed=seed)
        return batch_size

    def build_eval_env(self, env_num: int, split: str, seed: int) -> Any:
        if self.instances:
            log.info("eval env built (pinned instances)", count=len(self.instances), split=split, seed=seed)
            return self.instances
        if split == "test" and self._test_ids:
            log.info("eval env built (test split)", count=len(self._test_ids), seed=seed)
            return self._test_ids
        if self._val_ids:
            log.info("eval env built (val split)", count=len(self._val_ids), seed=seed)
            return self._val_ids
        log.info("eval env built", limit=env_num, split=split, seed=seed)
        return env_num

    def _extract_prompt_slot(self, skill_content: str) -> str:
        if not skill_content.startswith("---"):
            return skill_content
        from factory.skillopt.yaml_surface import load_yaml, extract_prompt_slots
        ann_path = self.skill_path.parent / "SKILL.annotations.yaml"
        if ann_path.exists():
            surface = load_yaml(ann_path)
            slots = extract_prompt_slots(surface)
            if slots:
                return next(iter(slots.values()))
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

        cmd = [
            str(script), "swebench",
            "--all",
            "--timeout", "7200",
            "--preserve",
        ]
        instances: list[str] = []
        if isinstance(env_manager, list):
            instances = env_manager
        elif self.instances:
            instances = self.instances

        if instances:
            for instance_id in instances:
                cmd += ["--include-task-name", f"*{instance_id}"]
        else:
            limit = int(env_manager) if env_manager else 0
            if limit > 0:
                cmd += ["--limit", str(limit)]

        env = dict(os.environ)
        prompt = self._extract_prompt_slot(skill_content)
        env["FACTORY_SKILL_B64"] = base64.b64encode(prompt.encode()).decode()
        if self.student_model:
            env["FACTORY_STUDENT_MODEL"] = self.student_model

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
        return ["bug_fix"]


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
    """Remove stale *-swebench-full.json files so the next run reads only fresh results."""
    if not _RESULTS_DIR.is_dir():
        return
    for f in _RESULTS_DIR.glob("*-swebench-full.json"):
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
        _RESULTS_DIR.glob("*-swebench-full.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _find_trial_dir(jobs_dir: str, instance_id: str) -> Path | None:
    """Find the trial directory for a given instance_id in the jobs dir."""
    if not jobs_dir:
        return None
    jobs_path = Path(jobs_dir)
    if not jobs_path.is_dir():
        return None
    for d in jobs_path.iterdir():
        if d.is_dir() and _TRIAL_SUFFIX_PATTERN.sub("", d.name) == instance_id:
            return d
    return None


def _parse_trial_trajectory(trial_dir: Path) -> str:
    """Extract formatted trajectory from Harbor trial session files + verifier output."""
    parts: list[str] = []

    session_files = list(trial_dir.rglob("sessions/projects/*/??*-*-*-*-*.jsonl"))
    if session_files:
        session = max(session_files, key=lambda p: p.stat().st_mtime)
        for line in session.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = entry.get("message", {})
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content", [])

            if role == "assistant" and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        parts.append(f"[assistant] {block['text'][:300]}")
                    elif block.get("type") == "tool_use":
                        tool = block.get("name", "")
                        inp = block.get("input", {})
                        if tool == "Bash":
                            parts.append(f"[bash] {str(inp.get('command', ''))[:200]}")
                        elif tool == "Read":
                            parts.append(f"[read] {inp.get('file_path', '')}")
                        elif tool == "Edit":
                            parts.append(f"[edit] {inp.get('file_path', '')}")
                        elif tool == "Write":
                            parts.append(f"[write] {inp.get('file_path', '')}")
                        else:
                            parts.append(f"[{tool}] {str(inp)[:100]}")

    verifier_stdout = trial_dir / "verifier" / "test-stdout.txt"
    if verifier_stdout.exists():
        vtext = verifier_stdout.read_text()
        summary_lines: list[str] = []
        for line in vtext.splitlines():
            if any(kw in line for kw in ["PASSED", "FAILED", "ERROR", "passed", "failed", "error"]):
                summary_lines.append(line)
        if summary_lines:
            parts.append("\n[VERIFIER TEST RESULTS]")
            parts.append("\n".join(summary_lines[:30]))

    return "\n".join(parts)


def _build_fail_reason(trial_dir: Path | None) -> str:
    """Derive a fail_reason string from the verifier test output."""
    if not trial_dir:
        return ""
    verifier_stdout = trial_dir / "verifier" / "test-stdout.txt"
    if not verifier_stdout.exists():
        return ""
    failed: list[str] = []
    for line in verifier_stdout.read_text().splitlines():
        if "FAILED" in line or "failed" in line:
            failed.append(line.strip())
    if not failed:
        return ""
    return f"{len(failed)} tests FAILED: " + "; ".join(failed[:5])


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

    results: list[RolloutResult] = []
    for task in tasks:
        instance_id = task.get("instance_id", "")
        resolved = task.get("resolved", False)

        trial_dir = _find_trial_dir(jobs_dir, instance_id)

        extras: dict[str, Any] = {}
        if trial_dir:
            trajectory = _parse_trial_trajectory(trial_dir)
            if trajectory:
                extras["trace_dump"] = trajectory

        fail_reason = task.get("fail_reason", "")
        if not fail_reason and not resolved and trial_dir:
            fail_reason = _build_fail_reason(trial_dir)

        results.append(RolloutResult(
            id=instance_id,
            hard=1.0 if resolved else 0.0,
            soft=float(task.get("score", 1.0 if resolved else 0.0)),
            n_turns=int(task.get("n_turns", 0)),
            fail_reason=fail_reason,
            task_type="bug_fix",
            extras=extras,
        ))

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "rollout_results.json").write_text(
        json.dumps([r.model_dump() for r in results], indent=2)
    )
    log.info("collected results", count=len(results))
    return results
