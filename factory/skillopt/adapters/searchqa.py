"""SearchQA adapter — direct LLM evaluation, no Harbor/Docker."""
from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import structlog

from factory.skillopt.adapter import EnvAdapter
from factory.skillopt.types import RolloutResult

log = structlog.get_logger()

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "benchmarks" / "searchqa" / "data"

_ANSWER_RE = re.compile(r"<answer>(.*?)</answer>", re.DOTALL)


def _load_jsonl(path: Path) -> list[dict]:
    items: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def _call_claude(prompt: str, timeout: int = 120, model: str = "haiku") -> str:
    if not shutil.which("claude"):
        log.warning("claude CLI not found")
        return ""
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", model, prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        log.warning("claude call failed", error=str(exc))
        return ""


def _extract_answer(text: str) -> str:
    m = _ANSWER_RE.search(text)
    if m:
        return m.group(1).strip()
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line:
            return line
    return text.strip()


class SearchQAAdapter(EnvAdapter):

    def __init__(self) -> None:
        self.data_dir: Path = _DEFAULT_DATA_DIR
        self.workers: int = 4
        self._train_data: list[dict] = []
        self._val_data: list[dict] = []

    def setup(self, cfg: dict) -> None:
        if cfg.get("dataset_dir"):
            self.data_dir = Path(cfg["dataset_dir"])
        self.workers = int(cfg.get("workers", 4))

    def _ensure_loaded(self) -> None:
        if not self._train_data:
            train_path = self.data_dir / "train.jsonl"
            if train_path.exists():
                self._train_data = _load_jsonl(train_path)
                log.info("loaded train data", count=len(self._train_data))
        if not self._val_data:
            val_path = self.data_dir / "val.jsonl"
            if val_path.exists():
                self._val_data = _load_jsonl(val_path)
                log.info("loaded val data", count=len(self._val_data))

    def build_train_env(self, batch_size: int, seed: int) -> Any:
        self._ensure_loaded()
        items = list(self._train_data)
        rng = random.Random(seed)
        rng.shuffle(items)
        batch = items[:batch_size]
        log.info("train env built", count=len(batch), seed=seed)
        return batch

    def build_eval_env(self, env_num: int, split: str, seed: int) -> Any:
        self._ensure_loaded()
        source = self._val_data if split in ("val", "eval") else self._train_data
        items = list(source)
        rng = random.Random(seed)
        rng.shuffle(items)
        batch = items[:env_num] if env_num > 0 else items
        log.info("eval env built", count=len(batch), split=split, seed=seed)
        return batch

    def _extract_skill_prompt(self, skill_content: str) -> str:
        """Extract the QA skill prompt from SKILL.md content.

        The rendered SKILL.md wraps the prompt inside a factory agent --task \"...\" block.
        For direct LLM calls we need just the prompt text, not the wrapper.
        If the content doesn't look like a rendered SKILL.md, return it as-is.
        """
        if not skill_content.startswith("---"):
            return skill_content
        match = re.search(
            r'factory agent builder --task "(.*?)"'
            r'\s*--project',
            skill_content,
            re.DOTALL,
        )
        if match:
            prompt = match.group(1)
            prompt = re.sub(r"\nRead: [^\n]+$", "", prompt)
            prompt = re.sub(r"\nWrite output to: [^\n]+$", "", prompt)
            return prompt.strip()
        return skill_content

    def rollout(
        self, env_manager: Any, skill_content: str, out_dir: str,
    ) -> list[RolloutResult]:
        from benchmarks.searchqa.evaluator import score_prediction

        items: list[dict] = env_manager
        if not items:
            log.warning("rollout called with empty env")
            return []

        skill_prompt = self._extract_skill_prompt(skill_content)
        log.info("rollout starting", items=len(items), workers=self.workers,
                 skill_chars=len(skill_prompt))

        def _run_one(item: dict) -> RolloutResult:
            prompt = (
                f"{skill_prompt}\n\n"
                f"## Question\n{item['question']}\n\n"
                f"## Search Results\n{item['context']}\n\n"
                f"Answer the question using ONLY the information in the search results.\n"
                f"Put your final answer inside <answer> tags. Example: <answer>Paris</answer>"
            )
            raw_response = _call_claude(prompt)
            prediction = _extract_answer(raw_response) if raw_response else ""
            gold_answers = item.get("answers", [])
            scores = score_prediction(prediction, gold_answers)

            return RolloutResult(
                id=item["id"],
                hard=scores["exact_match"],
                soft=scores["f1"],
                n_turns=1,
                fail_reason="" if raw_response else "no_response",
                task_type="question_answering",
                extras={"response": raw_response, "prediction": prediction},
            )

        results: list[RolloutResult] = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(_run_one, item): item["id"] for item in items}
            for future in as_completed(futures):
                item_id = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    log.warning("item failed", id=item_id, error=str(exc))
                    results.append(RolloutResult(
                        id=item_id,
                        hard=0.0,
                        soft=0.0,
                        fail_reason=str(exc),
                        task_type="question_answering",
                    ))

        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "rollout_results.json").write_text(
            json.dumps([r.model_dump() for r in results], indent=2)
        )
        log.info("rollout complete", count=len(results))
        return results

    def get_task_types(self) -> list[str]:
        return ["question_answering"]
