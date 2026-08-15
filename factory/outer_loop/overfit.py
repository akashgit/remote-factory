"""Overfit / cheating detection for evolved workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from factory.outer_loop.models import AuditResult

if TYPE_CHECKING:
    from factory.outer_loop.evaluator import SwarmEvaluator
    from factory.workflow.primitives import Workflow

log = structlog.get_logger()

OVERFIT_THRESHOLD = 0.15
CONSECUTIVE_OVERFIT_LIMIT = 3


class OverfitDetector:
    """Detects overfitting by comparing training vs holdout scores."""

    def __init__(self, threshold: float = OVERFIT_THRESHOLD) -> None:
        self._threshold = threshold
        self.history: list[tuple[int, float, float]] = []

    def audit_generation(
        self,
        generation: int,
        training_score: float,
        holdout_score: float,
    ) -> AuditResult:
        """Record per-generation holdout tracking and check for overfitting.

        Returns an AuditResult with the delta and overfit flag. Logs a warning
        if the overfit delta exceeds the threshold for CONSECUTIVE_OVERFIT_LIMIT
        consecutive generations.
        """
        if training_score > 0:
            delta = (training_score - holdout_score) / training_score
        else:
            delta = 0.0

        self.history.append((generation, training_score, holdout_score))

        overfit_flag = delta > self._threshold

        early_stop = False
        if len(self.history) >= CONSECUTIVE_OVERFIT_LIMIT:
            recent = self.history[-CONSECUTIVE_OVERFIT_LIMIT:]
            all_overfit = all(
                (t - h) / t > self._threshold if t > 0 else False
                for _, t, h in recent
            )
            if all_overfit:
                early_stop = True
                log.warning(
                    "overfit_early_stop",
                    consecutive=CONSECUTIVE_OVERFIT_LIMIT,
                    recent_deltas=[(t - h) / t if t > 0 else 0.0 for _, t, h in recent],
                )

        if overfit_flag:
            log.warning(
                "overfit_detected_generation",
                generation=generation,
                training_score=training_score,
                holdout_score=holdout_score,
                delta=delta,
            )
        else:
            log.info(
                "holdout_tracking",
                generation=generation,
                training_score=training_score,
                holdout_score=holdout_score,
                delta=delta,
            )

        details = (
            f"generation={generation} training={training_score:.4f} "
            f"holdout={holdout_score:.4f} delta={delta:.4f}"
        )

        return AuditResult(
            training_score=training_score,
            holdout_score=holdout_score,
            delta=delta,
            overfit_flag=overfit_flag,
            details=details if not early_stop else f"EARLY_STOP {details}",
        )

    def should_early_stop(self) -> bool:
        """Check if overfitting has persisted for too many consecutive generations."""
        if len(self.history) < CONSECUTIVE_OVERFIT_LIMIT:
            return False
        recent = self.history[-CONSECUTIVE_OVERFIT_LIMIT:]
        return all(
            (t - h) / t > self._threshold if t > 0 else False
            for _, t, h in recent
        )

    def audit(
        self,
        best_workflow: Workflow,
        training_instances: list[str],
        holdout_instances: list[str],
        evaluator: SwarmEvaluator,
        project_dir: str = "",
    ) -> AuditResult:
        """Run the best workflow on both training and holdout instances.

        Flags overfit if (training - holdout) / training > threshold.
        """
        train_result = evaluator.evaluate(best_workflow, project_dir, training_instances)
        holdout_result = evaluator.evaluate(best_workflow, project_dir, holdout_instances)

        training_score = train_result.score
        holdout_score = holdout_result.score

        if training_score > 0:
            delta = (training_score - holdout_score) / training_score
        else:
            delta = 0.0

        overfit_flag = delta > self._threshold

        if overfit_flag:
            log.warning(
                "overfit_detected",
                training_score=training_score,
                holdout_score=holdout_score,
                delta=delta,
                threshold=self._threshold,
            )
        else:
            log.info(
                "overfit_audit_passed",
                training_score=training_score,
                holdout_score=holdout_score,
                delta=delta,
            )

        details = (
            f"training={training_score:.4f} holdout={holdout_score:.4f} "
            f"delta={delta:.4f} threshold={self._threshold}"
        )

        return AuditResult(
            training_score=training_score,
            holdout_score=holdout_score,
            delta=delta,
            overfit_flag=overfit_flag,
            details=details,
        )
