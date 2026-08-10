"""OptimizationLoop — composable train loop for inner-outer optimization.

Composes Executor + Evaluator + Mutator with epoch/step structure,
history tracking, and gate evaluation per step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

from factory.optimization.gate import evaluate_gate
from factory.optimization.protocols import Evaluator, Executor, Mutator
from factory.optimization.surface import Surface
from factory.optimization.types import LoopConfig, Patch, StepRecord

log = structlog.get_logger()


@dataclass
class TrainResult:
    """Outcome of a full training run."""

    steps: list[StepRecord] = field(default_factory=list)
    best_score: float = 0.0
    best_step: int = 0
    final_score: float = 0.0


class OptimizationLoop:
    """Unified optimization loop composing Executor + Evaluator + Mutator.

    Runs an epoch/step training structure with per-step gate evaluation.
    """

    def __init__(
        self,
        project_dir: Path,
        surface: Surface,
        executor: Executor,
        evaluator: Evaluator,
        mutator: Mutator,
        config: LoopConfig | None = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.surface = surface
        self.executor = executor
        self.evaluator = evaluator
        self.mutator = mutator
        self.config = config or LoopConfig()
        self._history: list[StepRecord] = []
        self._global_step = 0
        self._current_score = 0.0
        self._best_score = 0.0
        self._best_step = 0

    def step(self) -> StepRecord:
        """Run one optimization step: execute → evaluate → mutate → gate."""
        self._global_step += 1
        score_start = self._current_score

        execution_result = self.executor.execute(
            self.project_dir, self.surface,
        )

        score_end = score_start
        eval_artifacts = [Path(a) for a in execution_result.artifacts]
        if eval_artifacts:
            eval_result = self.evaluator.parse_many(eval_artifacts)
            if eval_result.valid:
                score_end = eval_result.score

        patch: Patch | None = None
        if execution_result.returncode == 0:
            patch = self.mutator.propose(
                self.surface, execution_result, self._history,
            )

        gate = evaluate_gate(
            candidate_score=score_end,
            current_score=self._current_score,
            best_score=self._best_score,
            best_step=self._best_step,
            global_step=self._global_step,
        )

        verdict = "keep" if gate.accepted else "revert"
        if gate.accepted:
            self._current_score = score_end
            if score_end > self._best_score:
                self._best_score = score_end
                self._best_step = self._global_step
            if patch:
                for edit in patch.prompt_edits:
                    if edit.slot_name in self.surface.prompt_slots:
                        self.surface.prompt_slots[edit.slot_name] = edit.new_value

        record = StepRecord(
            step_number=self._global_step,
            score_start=score_start,
            score_end=score_end,
            score_delta=score_end - score_start,
            duration_s=execution_result.duration_s,
            cost_usd=execution_result.cost_usd,
            verdict=verdict,
            artifacts=execution_result.artifacts,
            patch=patch,
        )
        self._history.append(record)
        log.info(
            "loop.step",
            step=self._global_step,
            score_start=round(score_start, 4),
            score_end=round(score_end, 4),
            verdict=verdict,
        )
        return record

    def train(self) -> TrainResult:
        """Full training loop with epochs and steps."""
        for epoch in range(self.config.epochs):
            log.info("loop.epoch.start", epoch=epoch + 1, total=self.config.epochs)
            for step_in_epoch in range(self.config.steps_per_epoch):
                self.step()
            log.info(
                "loop.epoch.end",
                epoch=epoch + 1,
                current_score=round(self._current_score, 4),
                best_score=round(self._best_score, 4),
            )

        return TrainResult(
            steps=list(self._history),
            best_score=self._best_score,
            best_step=self._best_step,
            final_score=self._current_score,
        )

    def history(self) -> list[StepRecord]:
        """All step records from this session."""
        return list(self._history)
