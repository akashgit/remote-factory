"""Checkpoint/resume system for crash-resilient CEO orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict

log = structlog.get_logger()

_CHECKPOINT_FILE = "checkpoint.json"


class CheckpointState(BaseModel):
    """Serializable snapshot of a CEO cycle's progress."""

    model_config = ConfigDict(strict=True, extra="forbid")

    mode: str
    active_experiment_id: int | None
    completed_agents: list[str]
    pending_agents: list[str]
    last_eval_scores: dict[str, float]
    current_hypothesis: str | None
    completed_hypotheses: list[int] = []
    timestamp: str


def save_checkpoint(project_path: Path, state: CheckpointState) -> None:
    """Serialize checkpoint state to .factory/checkpoint.json."""
    from factory.store import ensure_factory_dir
    factory_dir = project_path / ".factory"
    ensure_factory_dir(factory_dir)
    checkpoint_path = factory_dir / _CHECKPOINT_FILE
    checkpoint_path.write_text(state.model_dump_json(indent=2))
    log.info("checkpoint.saved", path=str(checkpoint_path))


def load_checkpoint(project_path: Path) -> CheckpointState | None:
    """Load checkpoint from .factory/checkpoint.json, or None if absent/corrupt."""
    checkpoint_path = project_path / ".factory" / _CHECKPOINT_FILE
    if not checkpoint_path.exists():
        log.debug("checkpoint.not_found", path=str(checkpoint_path))
        return None
    try:
        data = json.loads(checkpoint_path.read_text())
        state = CheckpointState.model_validate(data)
    except (json.JSONDecodeError, Exception) as exc:
        log.warning("checkpoint.corrupt", path=str(checkpoint_path), error=str(exc))
        return None
    log.info("checkpoint.loaded", path=str(checkpoint_path))
    return state


def clear_checkpoint(project_path: Path) -> None:
    """Remove checkpoint file after a successful cycle."""
    checkpoint_path = project_path / ".factory" / _CHECKPOINT_FILE
    if checkpoint_path.exists():
        checkpoint_path.unlink()
        log.info("checkpoint.cleared", path=str(checkpoint_path))
    else:
        log.debug("checkpoint.clear_noop", path=str(checkpoint_path))


def format_checkpoint(state: CheckpointState) -> str:
    """Format a checkpoint as a human-readable string."""
    lines = [
        f"Mode:          {state.mode}",
        f"Experiment:    {state.active_experiment_id or 'none'}",
        f"Hypothesis:    {state.current_hypothesis or 'none'}",
        f"Completed:     {', '.join(state.completed_agents) or 'none'}",
        f"Pending:       {', '.join(state.pending_agents) or 'none'}",
    ]
    if state.completed_hypotheses:
        lines.append(f"Done hypotheses: {', '.join(str(h) for h in state.completed_hypotheses)}")
    if state.last_eval_scores:
        scores = ", ".join(f"{k}={v:.3f}" for k, v in state.last_eval_scores.items())
        lines.append(f"Eval scores:   {scores}")
    lines.append(f"Timestamp:     {state.timestamp}")
    return "\n".join(lines)
