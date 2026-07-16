"""Pydantic v2 strict models for the SkillOpt optimization loop."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


EditType = Literal["add_rule", "modify_rule", "remove_rule", "reword_section"]


class TraceReflection(BaseModel):
    """Per-trace LLM analysis result — one reflection per benchmark trace."""

    model_config = ConfigDict(strict=True, extra="forbid")

    instance_id: str
    benchmark: str
    resolved: bool
    trace_id: str
    diagnosis: str
    suggested_edit: str
    edit_type: EditType
    confidence: float


class EditProposal(BaseModel):
    """Consolidated edit proposal from aggregated reflections."""

    model_config = ConfigDict(strict=True, extra="forbid")

    edit_type: EditType
    location: str
    original_text: str
    proposed_text: str
    rationale: str
    supporting_instances: list[str]
    frequency: int


class CycleResult(BaseModel):
    """Full cycle outcome with before/after scores."""

    model_config = ConfigDict(strict=True, extra="forbid")

    cycle_id: int
    benchmark: str
    workflow_file: str
    score_before: float
    score_after: float | None
    accepted: bool
    reflections: list[TraceReflection]
    proposals_considered: list[EditProposal]
    proposals_applied: list[EditProposal]
