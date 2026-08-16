"""Benchmark subset selection for evolutionary search."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import structlog

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
