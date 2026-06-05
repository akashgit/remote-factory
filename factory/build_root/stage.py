"""Stage status model and terminal condition evaluation."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict


class StageState(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    GATED = "gated"
    FAILED = "failed"


class StageStatus(BaseModel):
    """Status of a single pipeline stage."""

    model_config = ConfigDict(strict=True, extra="forbid")

    stage: int
    name: str
    state: StageState = StageState.PENDING
    cycles: int = 0
    metric_current: int = 0
    metric_total: int = 0
    elapsed_seconds: float = 0.0


class PipelineStatus(BaseModel):
    """Overall build-root pipeline status."""

    model_config = ConfigDict(strict=True, extra="forbid")

    stages: list[StageStatus]
    stage_completed: int = 0


def _is_terminal_stage1(metrics: dict[str, Any]) -> bool:
    return metrics.get("failed", -1) == 0


def _is_terminal_stage2(metrics: dict[str, Any]) -> bool:
    recoverable = metrics.get("recoverable", 0)
    recovered = metrics.get("recovered", 0)
    dead_ends = metrics.get("dead_ends", 0)
    return recoverable <= recovered + dead_ends


def _is_terminal_stage3(metrics: dict[str, Any]) -> bool:
    return metrics.get("failed", -1) == 0


def _is_terminal_stage4(metrics: dict[str, Any]) -> bool:
    return metrics.get("failed", -1) == 0


_TERMINAL_CHECKS = {
    1: _is_terminal_stage1,
    2: _is_terminal_stage2,
    3: _is_terminal_stage3,
    4: _is_terminal_stage4,
}


def evaluate_terminal_condition(stage: int, metrics: dict[str, Any]) -> bool:
    """Return True if the given stage has reached its terminal condition."""
    check = _TERMINAL_CHECKS.get(stage)
    if check is None:
        return False
    return check(metrics)


def build_default_pipeline() -> PipelineStatus:
    """Create a fresh pipeline with all 4 stages pending."""
    return PipelineStatus(
        stages=[
            StageStatus(stage=1, name="DEP RESOLVE"),
            StageStatus(stage=2, name="ARTIFACT RECOVERY"),
            StageStatus(stage=3, name="COMPILE"),
            StageStatus(stage=4, name="TEST"),
        ],
        stage_completed=0,
    )
