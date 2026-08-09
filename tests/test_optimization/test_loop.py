"""Tests for factory.optimization.loop — OptimizationLoop step and train."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factory.inner_loop import EvalResult
from factory.optimization.loop import OptimizationLoop, TrainResult
from factory.optimization.surface import Surface
from factory.optimization.types import ExecutionResult, LoopConfig, Patch, StepRecord


class _CountingExecutor:
    def __init__(self) -> None:
        self.call_count = 0

    def execute(self, project_dir: Path, surface: Surface, **kwargs: Any) -> ExecutionResult:
        self.call_count += 1
        return ExecutionResult(returncode=0, artifacts=[], duration_s=1.0)


class _FixedScoreEvaluator:
    def __init__(self, score: float = 0.5) -> None:
        self.score = score

    def parse(self, artifact_path: Path) -> EvalResult:
        return EvalResult(score=self.score)

    def parse_many(self, artifact_paths: list[Path]) -> EvalResult:
        return EvalResult(score=self.score)

    def get_info(self) -> dict:
        return {"name": "fixed"}


class _IncreasingScoreEvaluator:
    def __init__(self) -> None:
        self._call = 0

    def parse(self, artifact_path: Path) -> EvalResult:
        self._call += 1
        return EvalResult(score=0.1 * self._call)

    def parse_many(self, artifact_paths: list[Path]) -> EvalResult:
        self._call += 1
        return EvalResult(score=0.1 * self._call)

    def get_info(self) -> dict:
        return {"name": "increasing"}


class _NoOpMutator:
    def propose(
        self,
        surface: Surface,
        execution_result: ExecutionResult,
        history: list[StepRecord],
    ) -> Patch:
        return Patch(reasoning="noop")


class TestOptimizationLoopStep:
    def test_single_step(self, tmp_path: Path) -> None:
        loop = OptimizationLoop(
            project_dir=tmp_path,
            surface=Surface(),
            executor=_CountingExecutor(),
            evaluator=_FixedScoreEvaluator(),
            mutator=_NoOpMutator(),
        )
        record = loop.step()
        assert record.step_number == 1
        assert record.verdict in ("keep", "revert")
        assert record.duration_s > 0

    def test_history_accumulates(self, tmp_path: Path) -> None:
        loop = OptimizationLoop(
            project_dir=tmp_path,
            surface=Surface(),
            executor=_CountingExecutor(),
            evaluator=_FixedScoreEvaluator(),
            mutator=_NoOpMutator(),
        )
        loop.step()
        loop.step()
        assert len(loop.history()) == 2
        assert loop.history()[0].step_number == 1
        assert loop.history()[1].step_number == 2


class TestOptimizationLoopTrain:
    def test_train_runs_correct_steps(self, tmp_path: Path) -> None:
        executor = _CountingExecutor()
        config = LoopConfig(epochs=2, steps_per_epoch=3)
        loop = OptimizationLoop(
            project_dir=tmp_path,
            surface=Surface(),
            executor=executor,
            evaluator=_FixedScoreEvaluator(),
            mutator=_NoOpMutator(),
            config=config,
        )
        result = loop.train()
        assert isinstance(result, TrainResult)
        assert len(result.steps) == 6
        assert executor.call_count == 6

    def test_train_default_config(self, tmp_path: Path) -> None:
        loop = OptimizationLoop(
            project_dir=tmp_path,
            surface=Surface(),
            executor=_CountingExecutor(),
            evaluator=_FixedScoreEvaluator(),
            mutator=_NoOpMutator(),
        )
        result = loop.train()
        assert len(result.steps) == 1
