"""SearchQA benchmark adapter — composes HarborExecutor + SearchQA evaluator.

Stub implementation that validates the type composition works.
The full adapter will connect to Harbor's SearchQA benchmark (400 train / 200 val)
and use SQuAD-style EM normalization for scoring.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import structlog

from factory.inner_loop import EvalResult
from factory.optimization.executors.harbor import HarborExecutor
from factory.optimization.surface import Surface
from factory.optimization.types import BenchmarkSplits, LoopConfig

log = structlog.get_logger()


class SearchQAEvaluator:
    """Parses SearchQA benchmark results from reward.json."""

    def __init__(self, target: float = 0.85) -> None:
        self.target = target

    def parse(self, artifact_path: Path) -> EvalResult:
        try:
            data = json.loads(artifact_path.read_text())
        except (json.JSONDecodeError, OSError):
            return EvalResult(score=0.0, valid=False)
        score = float(data.get("accuracy", data.get("score", 0.0)))
        return EvalResult(
            score=score,
            metrics={k: float(v) for k, v in data.items() if isinstance(v, (int, float))},
            valid=True,
            artifacts=[str(artifact_path)],
        )

    def parse_many(self, artifact_paths: list[Path]) -> EvalResult:
        best = EvalResult(score=0.0, valid=False)
        for p in artifact_paths:
            result = self.parse(p)
            if result.score > best.score:
                best = result
        return best

    def get_info(self) -> dict:
        return {
            "benchmark": "searchqa",
            "target": self.target,
            "metrics": ["accuracy", "em", "f1"],
        }


def create_searchqa_splits(
    tasks_dir: Path,
    train_ratio: float = 0.6,
    dev_ratio: float = 0.2,
    eval_ratio: float = 0.1,
    test_ratio: float = 0.1,
    seed: int = 42,
) -> BenchmarkSplits:
    """Partition SearchQA task IDs into train/dev/eval/test splits."""
    task_ids = sorted(d.name for d in tasks_dir.iterdir() if d.is_dir())
    rng = random.Random(seed)
    rng.shuffle(task_ids)
    n = len(task_ids)
    n_train = int(n * train_ratio)
    n_dev = int(n * (train_ratio + dev_ratio)) - n_train
    n_eval = int(n * (train_ratio + dev_ratio + eval_ratio)) - n_train - n_dev
    idx = 0
    train_ids = task_ids[idx : idx + n_train]
    idx += n_train
    dev_ids = task_ids[idx : idx + n_dev]
    idx += n_dev
    eval_ids = task_ids[idx : idx + n_eval]
    idx += n_eval
    test_ids = task_ids[idx:]
    return BenchmarkSplits(
        train_ids=train_ids,
        dev_ids=dev_ids,
        eval_ids=eval_ids,
        test_ids=test_ids,
    )


def build_searchqa_surface(skill_path: Path | None = None) -> Surface:
    """Build a Surface configured for SearchQA optimization."""
    prompt_slots: dict[str, str] = {}
    if skill_path and skill_path.exists():
        prompt_slots["skill"] = skill_path.read_text()
    return Surface(prompt_slots=prompt_slots)


def build_searchqa_config(
    epochs: int = 3,
    steps_per_epoch: int = 5,
) -> LoopConfig:
    """Build a LoopConfig for SearchQA optimization."""
    return LoopConfig(epochs=epochs, steps_per_epoch=steps_per_epoch)


def build_searchqa_executor(
    harbor_script: str = "./run-harbor.sh",
) -> HarborExecutor:
    """Build a HarborExecutor configured for SearchQA."""
    return HarborExecutor(harbor_script=harbor_script)
