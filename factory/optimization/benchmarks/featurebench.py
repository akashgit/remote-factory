"""FeatureBench benchmark adapter — evaluator, surface, config, and executor builders.

Mirrors the SearchQA adapter pattern but targets the FeatureBench Harbor benchmark
(feature implementation in Python codebases). Uses ``resolved_rate`` as the primary
metric instead of ``accuracy``.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from factory.inner_loop import EvalResult
from factory.optimization.benchmarks.harbor import HarborBenchmark
from factory.optimization.surface import Surface
from factory.optimization.types import LoopConfig
from factory.workflow.contributed.featurebench import workflow as featurebench_workflow

log = structlog.get_logger()


class FeatureBenchEvaluator:
    """Parses FeatureBench benchmark results from verifier output."""

    def __init__(self, target: float = 0.85) -> None:
        self.target = target

    def parse(self, artifact_path: Path) -> EvalResult:
        try:
            data = json.loads(artifact_path.read_text())
        except (json.JSONDecodeError, OSError):
            return EvalResult(score=0.0, valid=False)
        score = float(data.get("resolved_rate", data.get("score", 0.0)))
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
            "benchmark": "featurebench",
            "target": self.target,
            "metrics": ["resolved_rate", "test_pass_rate"],
        }


def build_featurebench_surface(workflow_path: Path | None = None) -> Surface:
    """Build a Surface configured for FeatureBench optimization."""
    wf = featurebench_workflow()
    frozen = frozenset({"study", "auto_merge"})
    return Surface(workflow=wf, frozen_nodes=frozen, prompt_slots={})


def build_featurebench_config(
    epochs: int = 3,
    steps_per_epoch: int = 5,
) -> LoopConfig:
    """Build a LoopConfig for FeatureBench optimization."""
    return LoopConfig(epochs=epochs, steps_per_epoch=steps_per_epoch)


def build_featurebench_executor(
    git_ref: str = "main",
    subset_dir: str | Path | None = None,
    concurrency: int = 5,
    docker_host: str | None = None,
    model: str = "opus",
) -> HarborBenchmark:
    """Build a HarborBenchmark configured for FeatureBench."""
    return HarborBenchmark(
        git_ref=git_ref,
        subset_dir=subset_dir,
        concurrency=concurrency,
        docker_host=docker_host,
        model=model,
        agent_class="factory_harbor_agent:FeaturebenchFactoryCeo",
        dataset="featurebench",
    )
