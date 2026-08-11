"""Value types for the unified optimization loop.

Absorbs concepts from:
- CycleRecord (cycle_analyzer.py) → StepRecord
- overwrite.py mutation dicts → GraphMutation
- InnerLoopConfig/OuterLoopConfig (models.py) → LoopConfig
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from factory.models import AggregateMethod

SplitName = Literal["train", "dev", "eval", "test"]

_SPLIT_NAMES: tuple[SplitName, ...] = ("train", "dev", "eval", "test")


@dataclass
class BenchmarkSplits:
    """Task-ID partitions for train/dev/eval/test splits."""

    train_ids: list[str] = field(default_factory=list)
    dev_ids: list[str] = field(default_factory=list)
    eval_ids: list[str] = field(default_factory=list)
    test_ids: list[str] = field(default_factory=list)

    def get_ids(self, split: SplitName) -> list[str]:
        return list(getattr(self, f"{split}_ids"))

    def validate(self) -> list[str]:
        warnings: list[str] = []
        splits = {name: set(self.get_ids(name)) for name in _SPLIT_NAMES}
        for i, (n1, s1) in enumerate(splits.items()):
            for n2, s2 in list(splits.items())[i + 1 :]:
                overlap = s1 & s2
                if overlap:
                    warnings.append(
                        f"overlap between {n1} and {n2}: {sorted(overlap)}"
                    )
        if not self.dev_ids:
            warnings.append("dev split is empty")
        if not self.test_ids:
            warnings.append("test split is empty")
        return warnings

    @classmethod
    def from_jsonl_dir(cls, splits_dir: Path) -> BenchmarkSplits:
        kwargs: dict[str, list[str]] = {}
        for name in _SPLIT_NAMES:
            fpath = splits_dir / f"{name}.jsonl"
            ids: list[str] = []
            if fpath.exists():
                for line in fpath.read_text().splitlines():
                    line = line.strip()
                    if line:
                        ids.append(json.loads(line)["id"])
            kwargs[f"{name}_ids"] = ids
        return cls(**kwargs)

    def to_jsonl_dir(self, splits_dir: Path) -> None:
        splits_dir.mkdir(parents=True, exist_ok=True)
        for name in _SPLIT_NAMES:
            ids = self.get_ids(name)
            fpath = splits_dir / f"{name}.jsonl"
            fpath.write_text(
                "\n".join(json.dumps({"id": tid}) for tid in ids) + "\n"
                if ids
                else ""
            )


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
class TaskResult:
    """Per-task result from a benchmark execution."""

    task_id: str
    reward: float
    predicted: str = ""
    gold: str = ""
    question: str = ""


@dataclass
class ExecutionResult:
    """Result of running one optimization step via an Executor."""

    returncode: int
    artifacts: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    cost_usd: float = 0.0
    task_results: list[TaskResult] = field(default_factory=list)


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
