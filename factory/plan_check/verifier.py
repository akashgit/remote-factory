"""Verify acceptance criteria by running actual checks."""

from __future__ import annotations

from pathlib import Path

from factory.plan_check.models import AcceptanceCriterion, CriterionResult


def verify_criteria(
    criteria: list[AcceptanceCriterion],
    project_path: Path,
    baseline_sha: str | None = None,
) -> list[CriterionResult]:
    raise NotImplementedError
