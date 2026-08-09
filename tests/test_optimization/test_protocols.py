"""Tests for factory.optimization.protocols — runtime_checkable verification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factory.inner_loop import CirclePackingEvaluator, EvalResult
from factory.optimization.protocols import Evaluator, Executor, Mutator
from factory.optimization.surface import Surface
from factory.optimization.types import ExecutionResult, Patch, StepRecord


class _StubExecutor:
    def execute(self, project_dir: Path, surface: Surface, **kwargs: Any) -> ExecutionResult:
        return ExecutionResult(returncode=0)


class _StubEvaluator:
    def parse(self, artifact_path: Path) -> EvalResult:
        return EvalResult(score=0.5)

    def parse_many(self, artifact_paths: list[Path]) -> EvalResult:
        return EvalResult(score=0.5)

    def get_info(self) -> dict:
        return {}


class _StubMutator:
    def propose(
        self,
        surface: Surface,
        execution_result: ExecutionResult,
        history: list[StepRecord],
    ) -> Patch:
        return Patch()


class _NotAnExecutor:
    pass


class _NotAnEvaluator:
    def parse(self, path: Path) -> None:
        pass


class TestExecutorProtocol:
    def test_stub_satisfies(self) -> None:
        assert isinstance(_StubExecutor(), Executor)

    def test_non_executor_rejected(self) -> None:
        assert not isinstance(_NotAnExecutor(), Executor)


class TestEvaluatorProtocol:
    def test_stub_satisfies(self) -> None:
        assert isinstance(_StubEvaluator(), Evaluator)

    def test_circle_packing_satisfies(self) -> None:
        assert isinstance(CirclePackingEvaluator(), Evaluator)

    def test_incomplete_rejected(self) -> None:
        assert not isinstance(_NotAnEvaluator(), Evaluator)


class TestMutatorProtocol:
    def test_stub_satisfies(self) -> None:
        assert isinstance(_StubMutator(), Mutator)

    def test_non_mutator_rejected(self) -> None:
        assert not isinstance(object(), Mutator)
