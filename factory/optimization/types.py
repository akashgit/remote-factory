"""Value types for the unified optimization loop.

Absorbs concepts from:
- CycleRecord (cycle_analyzer.py) → StepRecord
- overwrite.py mutation dicts → GraphMutation
- InnerLoopConfig/OuterLoopConfig (models.py) → LoopConfig
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from factory.models import AggregateMethod


@dataclass
class SlotEdit:
    """A single prompt-slot mutation."""

    slot_name: str
    old_value: str
    new_value: str


class GraphMutation(BaseModel):
    """Typed version of the raw mutation dicts from workflow/overwrite.py."""

    model_config = ConfigDict(strict=True, extra="forbid")

    op: Literal["update_node", "remove_node", "add_edge", "remove_edge"]
    node_id: str | None = None
    field: str | None = None
    value: Any | None = None
    source: str | None = None
    target: str | None = None


@dataclass
class Patch:
    """Proposed changes to a Surface: prompt edits + graph mutations."""

    prompt_edits: list[SlotEdit] = field(default_factory=list)
    graph_mutations: list[GraphMutation] = field(default_factory=list)
    reasoning: str = ""


@dataclass
class ExecutionResult:
    """Result of running one optimization step via an Executor."""

    returncode: int
    artifacts: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    cost_usd: float = 0.0


@dataclass
class GateResult:
    """Accept/reject decision from the gate."""

    accepted: bool
    reason: str
    candidate_score: float
    current_score: float
    best_score: float


@dataclass
class StepRecord:
    """What an outer-loop optimizer sees after one step.

    Slimmed version of CycleRecord focused on optimizer needs.
    """

    step_number: int
    score_start: float | None = None
    score_end: float | None = None
    score_delta: float | None = None
    duration_s: float = 0.0
    cost_usd: float = 0.0
    verdict: str | None = None
    artifacts: list[str] = field(default_factory=list)
    patch: Patch | None = None


class LoopConfig(BaseModel):
    """Unified loop configuration composing InnerLoopConfig + OuterLoopConfig fields."""

    model_config = ConfigDict(strict=True, extra="forbid")

    epochs: int = 1
    steps_per_epoch: int = 1
    plateau_threshold: int = 3
    aggregate: AggregateMethod = AggregateMethod.mean
    inner_surfaces: list[str] = []
    outer_surfaces: list[str] = []
    frozen_nodes: frozenset[str] = frozenset()
    max_inner_runs_per_cycle: int | None = None
