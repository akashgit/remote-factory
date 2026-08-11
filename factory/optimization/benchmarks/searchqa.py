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
    dev_size: int = 80,
    eval_ratio: float = 0.10,
    test_ratio: float = 0.10,
    seed: int = 42,
) -> BenchmarkSplits:
    """Partition SearchQA task IDs into train/dev/eval/test splits.

    dev is a subset of train (not disjoint). eval and test are held out.
    """
    task_ids = sorted(d.name for d in tasks_dir.iterdir() if d.is_dir())
    rng = random.Random(seed)
    rng.shuffle(task_ids)
    n = len(task_ids)
    n_eval = int(n * eval_ratio)
    n_test = int(n * test_ratio)
    n_train = n - n_eval - n_test

    train_ids = task_ids[:n_train]
    eval_ids = task_ids[n_train : n_train + n_eval]
    test_ids = task_ids[n_train + n_eval :]
    dev_ids = train_ids[:dev_size]

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
