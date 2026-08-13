"""Checkpoint and state management."""

import json
from pathlib import Path
from typing import Any


def load_state(project_path: Path) -> dict[str, Any]:
    """Load current training state.

    Args:
        project_path: Path to the project root

    Returns:
        State dict with iteration, best_score, best_iteration
    """
    state_file = project_path / ".factory/lumen/state.json"

    if not state_file.exists():
        return {
            "iteration": 0,
            "best_score": None,
            "best_iteration": None,
        }

    with open(state_file) as f:
        return json.load(f)


def save_state(project_path: Path, state: dict[str, Any]) -> None:
    """Save training state.

    Args:
        project_path: Path to the project root
        state: State dict to save
    """
    state_file = project_path / ".factory/lumen/state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)

    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def get_verl_checkpoint_path(project_path: Path) -> Path | None:
    """Get the latest VERL checkpoint path, or None if no checkpoint exists."""
    ckpt_dir = project_path / ".factory/lumen/checkpoints/verl/latest"
    if ckpt_dir.exists():
        return ckpt_dir
    return None


def get_verl_rollout_log(project_path: Path, step: int = 0) -> Path | None:
    """Get the VERL rollout log for a given step."""
    log_path = project_path / f".factory/lumen/checkpoints/verl/rollouts/{step}.jsonl"
    if log_path.exists():
        return log_path
    return None
