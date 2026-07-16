"""Pydantic v2 strict models for the SkillOpt optimization loop."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


EditOp = Literal["append", "insert_after", "replace", "delete"]


class Edit(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    op: EditOp
    content: str
    target: str = ""
    support_count: int | None = None
    source_type: Literal["failure", "success"] | None = None


class Patch(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    edits: list[Edit]
    reasoning: str = ""
    ranking_details: dict | None = None


class RolloutResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    id: str
    hard: float
    soft: float
    n_turns: int = 0
    fail_reason: str = ""
    task_type: str = ""
    trace_id: str = ""
    extras: dict = Field(default_factory=dict)


class FailureSummaryEntry(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    failure_type: str
    count: int = 0
    description: str = ""


class RawPatch(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    patch: Patch
    source_type: Literal["failure", "success"] = "failure"
    batch_size: int = 0
    failure_summary: list[FailureSummaryEntry] = Field(default_factory=list)


GateAction = Literal["accept_new_best", "accept", "reject"]


class GateResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    action: GateAction
    current_skill: str
    current_score: float
    best_skill: str
    best_score: float
    best_step: int
