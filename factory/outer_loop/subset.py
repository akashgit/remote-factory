"""Benchmark subset selection for evolutionary search."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

import structlog

if TYPE_CHECKING:
    from factory.outer_loop.evaluator import EvaluatorFn
    from factory.workflow.primitives import Workflow

log = structlog.get_logger()


@runtime_checkable
class SubsetSelector(Protocol):
    """Protocol for selecting which benchmark instances to evaluate per generation."""

    def select(
        self, all_instances: list[str], generation: int, budget_remaining: int
    ) -> list[str]: ...


class FixedSubsetSelector:
    """Always returns the configured training instances."""

    def __init__(self, training_instances: list[str]) -> None:
        self._training_instances = list(training_instances)

    def select(
        self, all_instances: list[str], generation: int, budget_remaining: int
    ) -> list[str]:
        return list(self._training_instances)


class CalibratedSubsetSelector:
    """Selects training/holdout instances based on difficulty calibration.

    Runs a seed workflow on all available instances, filters to a target
    difficulty range, and stratifies by repository prefix.
    """

    def __init__(
        self,
        training_size: int = 10,
        holdout_size: int = 5,
        difficulty_range: tuple[float, float] = (0.3, 0.7),
    ) -> None:
        self._training_size = training_size
        self._holdout_size = holdout_size
        self._difficulty_range = difficulty_range
        self._training_instances: list[str] = []
        self._holdout_instances: list[str] = []
        self._calibrated = False
        self._calibration_scores: dict[str, float] = {}

    @property
    def training_instances(self) -> list[str]:
        return list(self._training_instances)

    @property
    def holdout_instances(self) -> list[str]:
        return list(self._holdout_instances)

    @property
    def calibration_scores(self) -> dict[str, float]:
        return dict(self._calibration_scores)

    @property
    def is_calibrated(self) -> bool:
        return self._calibrated

    def calibrate(
        self,
        all_instances: list[str],
        seed_workflow: Workflow,
        evaluator_fn: EvaluatorFn,
        project_dir: str = "",
    ) -> dict[str, float]:
        """Run the seed workflow on all instances and select training/holdout splits.

        Returns a dict mapping instance IDs to their baseline scores.
        """
        scores: dict[str, float] = {}
        for instance_id in all_instances:
            try:
                result = evaluator_fn(seed_workflow, project_dir, [instance_id])
                scores[instance_id] = result.benchmark_score
            except Exception:
                log.warning("calibration_eval_failed", instance=instance_id, exc_info=True)
                scores[instance_id] = 0.0

        self._calibration_scores = scores

        lo, hi = self._difficulty_range
        in_range = [iid for iid, s in scores.items() if lo <= s <= hi]

        if len(in_range) < self._training_size:
            log.warning(
                "calibration_widening_range",
                in_range=len(in_range),
                needed=self._training_size,
                original_range=self._difficulty_range,
            )
            lo_wide = max(lo - 0.1, 0.0)
            hi_wide = min(hi + 0.1, 1.0)
            in_range = [iid for iid, s in scores.items() if lo_wide <= s <= hi_wide]

        in_range.sort(key=lambda iid: _repo_prefix(iid))

        total_needed = self._training_size + self._holdout_size
        if len(in_range) >= total_needed:
            self._training_instances = _stratified_select(
                in_range, self._training_size, scores
            )
            remaining = [i for i in in_range if i not in set(self._training_instances)]
            self._holdout_instances = _stratified_select(
                remaining, self._holdout_size, scores
            )
        else:
            split = max(1, int(len(in_range) * self._training_size / total_needed))
            self._training_instances = in_range[:split]
            self._holdout_instances = in_range[split:]

        self._calibrated = True

        log.info(
            "calibration_complete",
            total_instances=len(all_instances),
            in_difficulty_range=len(in_range),
            training=len(self._training_instances),
            holdout=len(self._holdout_instances),
        )
        return scores

    def select(
        self, all_instances: list[str], generation: int, budget_remaining: int
    ) -> list[str]:
        if self._calibrated:
            return list(self._training_instances)
        return list(all_instances[:self._training_size])


def _repo_prefix(instance_id: str) -> str:
    """Extract the repository prefix from an instance ID (e.g., 'pydantic__pydantic-1234' -> 'pydantic__pydantic')."""
    parts = instance_id.rsplit("-", 1)
    return parts[0] if len(parts) > 1 else instance_id


def _stratified_select(
    candidates: list[str],
    count: int,
    scores: dict[str, float],
) -> list[str]:
    """Select instances with even distribution across repository prefixes."""
    by_repo: dict[str, list[str]] = {}
    for iid in candidates:
        prefix = _repo_prefix(iid)
        by_repo.setdefault(prefix, []).append(iid)

    selected: list[str] = []
    repos = list(by_repo.keys())
    idx = 0
    while len(selected) < count and any(by_repo.values()):
        repo = repos[idx % len(repos)]
        if by_repo[repo]:
            selected.append(by_repo[repo].pop(0))
        idx += 1
        if idx > count * len(repos):
            break

    return selected
