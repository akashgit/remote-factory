"""Checkpoint persistence for crash-resilient evolutionary search."""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict, Field

from factory.outer_loop.models import (
    GenerationSummary,
    HyperparameterRecord,
    Individual,
    MutationRecord,
)

log = structlog.get_logger()


class CheckpointData(BaseModel):
    """Serializable checkpoint of evolution state after a generation."""

    model_config = ConfigDict(strict=True, extra="forbid")

    generation: int
    population: list[Individual]
    best_individual: Individual | None = None
    score_trajectory: list[float] = Field(default_factory=list)
    mutation_history: list[MutationRecord] = Field(default_factory=list)
    budget_consumed: int = 0
    budget_total: int = 0
    calibration_path: str = ""
    generation_summaries: list[GenerationSummary] = Field(default_factory=list)
    hyperparameter_history: list[HyperparameterRecord] = Field(default_factory=list)


def save_checkpoint(
    checkpoint_dir: Path,
    data: CheckpointData,
) -> Path:
    """Write checkpoint atomically (write to .tmp then rename)."""
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    filename = f"checkpoint_gen_{data.generation}.json"
    path = checkpoint_dir / filename
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data.model_dump(mode="json"), indent=2, default=str))
    tmp_path.rename(path)
    log.info("checkpoint_saved", generation=data.generation, path=str(path))
    return path


def load_latest_checkpoint(checkpoint_dir: Path) -> CheckpointData | None:
    """Load the latest checkpoint from a directory, or None if none exist."""
    checkpoints = sorted(checkpoint_dir.glob("checkpoint_gen_*.json"))
    if not checkpoints:
        return None
    latest = checkpoints[-1]
    log.info("checkpoint_loading", path=str(latest))
    raw = json.loads(latest.read_text())
    return CheckpointData.model_validate(raw, strict=False)
