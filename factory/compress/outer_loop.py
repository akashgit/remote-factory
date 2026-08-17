"""CompressOuterLoop — outer loop for compression optimization."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from factory.compress.inner_loop import CompressInnerLoop
from factory.strategy import detect_research_plateau

log = structlog.get_logger()

_NO_IMPROVEMENT_LIMIT = 3


@dataclass
class OuterLoopResult:
    """Summary of a completed outer loop run."""

    best_technique: dict | None
    trajectory: list[dict]
    total_cost: float
    convergence_reason: str
    cycles_completed: int = 0
    plateau_count: int = 0


class CompressOuterLoop:
    """Outer loop wrapping CompressInnerLoop with plateau detection and directive escalation."""

    def __init__(
        self,
        inner: CompressInnerLoop,
        budget: int = 20,
    ) -> None:
        self.inner = inner
        self.budget = budget
        self._cycle = 0
        self._plateau_count = 0
        self._best_score: float | None = None

    def run(self) -> OuterLoopResult:
        """Run inner loop cycles until convergence or budget exhaustion."""
        while not self._converged():
            plateau = self._detect_plateau()
            directives = self._analyze_and_steer(plateau)
            self.inner.step(directives=directives if directives else None)
            self._cycle += 1

            trajectory = self.inner.score_trajectory()
            if trajectory:
                current = trajectory[-1]
                if self._best_score is None or current > self._best_score:
                    self._best_score = current

            log.info(
                "compress_outer_cycle",
                cycle=self._cycle,
                plateau_count=self._plateau_count,
                best_score=self._best_score,
            )

        return self._summarize()

    def _analyze_and_steer(self, plateau_detected: bool) -> dict:
        """Analyze technique history and generate directives based on plateau state."""
        if not plateau_detected:
            return {}

        self._plateau_count += 1
        log.info("compress_plateau_escalation", plateau_count=self._plateau_count)

        if self._plateau_count == 1:
            return {
                "focus": "Try alternative compression techniques",
                "escalation": "inner",
                "prioritize": "quality_retention",
            }

        if self._plateau_count == 2:
            return {
                "focus": "Restructure evaluation approach",
                "escalation": "outer",
                "prioritize": "compression_ratio",
            }

        return {
            "focus": "Converging — exhausted mutation surfaces",
            "escalation": "converge",
        }

    def _converged(self) -> bool:
        if self._cycle >= self.budget:
            return True
        if self._plateau_count >= 3:
            return True
        return False

    def _detect_plateau(self) -> bool:
        history = self.inner.technique_history()
        if len(history) < 2:
            return False
        summaries = [
            {"metric_value": h["score"]}
            for h in history
            if h["score"] is not None
        ]
        return detect_research_plateau(summaries, threshold=_NO_IMPROVEMENT_LIMIT)

    def _summarize(self) -> OuterLoopResult:
        reason = "budget_exhausted"
        if self._plateau_count >= 3:
            reason = "converged"
        elif self._cycle >= self.budget:
            reason = "max_cycles"

        return OuterLoopResult(
            best_technique=self.inner.best_technique(),
            trajectory=self.inner.compression_trajectory(),
            total_cost=self.inner.total_cost(),
            convergence_reason=reason,
            cycles_completed=self._cycle,
            plateau_count=self._plateau_count,
        )
