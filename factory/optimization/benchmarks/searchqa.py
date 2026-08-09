"""SearchQA benchmark adapter — composes HarborExecutor + SearchQA evaluator.

Stub implementation that validates the type composition works.
The full adapter will connect to Harbor's SearchQA benchmark (400 train / 200 val)
and use SQuAD-style EM normalization for scoring.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from factory.inner_loop import EvalResult
from factory.optimization.executors.harbor import HarborExecutor
from factory.optimization.surface import Surface
from factory.optimization.types import LoopConfig

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
