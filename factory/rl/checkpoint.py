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
    state_file = project_path / ".factory/rl/state.json"

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
    state_file = project_path / ".factory/rl/state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)

    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)
