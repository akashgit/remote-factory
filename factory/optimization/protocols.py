"""Three pluggable protocols defining the optimization loop contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from factory.inner_loop import EvalResult
from factory.optimization.surface import Surface
from factory.optimization.types import ExecutionResult, Patch, SplitName, StepRecord


@runtime_checkable
class Executor(Protocol):
    """Runs one inner-loop cycle and returns structured results."""

    def execute(
        self, project_dir: Path, surface: Surface, split: SplitName | None = None, **kwargs: Any
    ) -> ExecutionResult: ...


@runtime_checkable
class Evaluator(Protocol):
    """Parses evaluator-specific output artifacts into scores.

    Mirrors the protocol from factory.inner_loop — same method signatures
    so existing implementations (CirclePackingEvaluator) satisfy both.
    """

    def parse(self, artifact_path: Path) -> EvalResult: ...

    def parse_many(self, artifact_paths: list[Path]) -> EvalResult: ...

    def get_info(self) -> dict: ...


@runtime_checkable
class Mutator(Protocol):
    """Proposes changes to a Surface based on execution results and history."""

    def propose(
        self,
        surface: Surface,
        execution_result: ExecutionResult,
        history: list[StepRecord],
    ) -> Patch: ...
