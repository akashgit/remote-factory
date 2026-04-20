"""Tests for factory.checkpoint — save/load/clear/format + CLI commands."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from factory.checkpoint import (
    CheckpointState,
    clear_checkpoint,
    format_checkpoint,
    load_checkpoint,
    save_checkpoint,
)


@pytest.fixture
def checkpoint_project(tmp_path: Path) -> Path:
    """Create a minimal project directory with .factory/."""
    project = tmp_path / "ckpt-project"
    project.mkdir()
    (project / ".factory").mkdir()
    return project


@pytest.fixture
def sample_state() -> CheckpointState:
    """Return a sample CheckpointState for testing."""
    return CheckpointState(
        mode="improve",
        active_experiment_id=38,
        completed_agents=["researcher", "strategist"],
        pending_agents=["builder", "reviewer", "evaluator"],
        last_eval_scores={"tests": 0.95, "lint": 1.0},
        current_hypothesis="Add checkpoint serialization",
        timestamp="2026-04-20T12:00:00",
    )


# ── model tests ──────────────────────────────────────────────────


def test_checkpoint_state_strict() -> None:
    """CheckpointState rejects extra fields."""
    with pytest.raises(Exception):
        CheckpointState(
            mode="improve",
            active_experiment_id=None,
            completed_agents=[],
            pending_agents=[],
            last_eval_scores={},
            current_hypothesis=None,
            timestamp="2026-01-01T00:00:00",
            extra_field="bad",
        )


def test_checkpoint_state_nullable_fields() -> None:
    """CheckpointState allows None for optional fields."""
    state = CheckpointState(
        mode="build",
        active_experiment_id=None,
        completed_agents=[],
        pending_agents=["researcher"],
        last_eval_scores={},
        current_hypothesis=None,
        timestamp="2026-01-01T00:00:00",
    )
    assert state.active_experiment_id is None
    assert state.current_hypothesis is None


# ── save / load / clear ──────────────────────────────────────────


def test_save_and_load(checkpoint_project: Path, sample_state: CheckpointState) -> None:
    """save_checkpoint writes JSON, load_checkpoint round-trips it."""
    save_checkpoint(checkpoint_project, sample_state)

    checkpoint_path = checkpoint_project / ".factory" / "checkpoint.json"
    assert checkpoint_path.exists()

    loaded = load_checkpoint(checkpoint_project)
    assert loaded is not None
    assert loaded == sample_state
    assert loaded.mode == "improve"
    assert loaded.active_experiment_id == 38
    assert loaded.completed_agents == ["researcher", "strategist"]
    assert loaded.pending_agents == ["builder", "reviewer", "evaluator"]
    assert loaded.last_eval_scores == {"tests": 0.95, "lint": 1.0}
    assert loaded.current_hypothesis == "Add checkpoint serialization"


def test_load_missing(checkpoint_project: Path) -> None:
    """load_checkpoint returns None when no checkpoint exists."""
    assert load_checkpoint(checkpoint_project) is None


def test_clear(checkpoint_project: Path, sample_state: CheckpointState) -> None:
    """clear_checkpoint removes the file."""
    save_checkpoint(checkpoint_project, sample_state)
    checkpoint_path = checkpoint_project / ".factory" / "checkpoint.json"
    assert checkpoint_path.exists()

    clear_checkpoint(checkpoint_project)
    assert not checkpoint_path.exists()


def test_clear_noop(checkpoint_project: Path) -> None:
    """clear_checkpoint does not raise when no checkpoint exists."""
    clear_checkpoint(checkpoint_project)  # should not raise


def test_save_creates_factory_dir(tmp_path: Path, sample_state: CheckpointState) -> None:
    """save_checkpoint creates .factory/ if it doesn't exist."""
    project = tmp_path / "bare-project"
    project.mkdir()
    save_checkpoint(project, sample_state)
    assert (project / ".factory" / "checkpoint.json").exists()


# ── format ───────────────────────────────────────────────────────


def test_format_full(sample_state: CheckpointState) -> None:
    """format_checkpoint includes all fields."""
    output = format_checkpoint(sample_state)
    assert "improve" in output
    assert "38" in output
    assert "researcher" in output
    assert "builder" in output
    assert "Add checkpoint serialization" in output
    assert "tests=0.950" in output
    assert "lint=1.000" in output
    assert "2026-04-20T12:00:00" in output


def test_format_empty_scores() -> None:
    """format_checkpoint omits eval scores line when empty."""
    state = CheckpointState(
        mode="discover",
        active_experiment_id=None,
        completed_agents=[],
        pending_agents=[],
        last_eval_scores={},
        current_hypothesis=None,
        timestamp="2026-01-01T00:00:00",
    )
    output = format_checkpoint(state)
    assert "Eval scores" not in output
    assert "discover" in output


# ── CLI integration ──────────────────────────────────────────────


def test_cli_checkpoint_show_none(checkpoint_project: Path) -> None:
    """factory checkpoint <path> shows 'No checkpoint' when empty."""
    from factory.cli import main

    code = main(["checkpoint", str(checkpoint_project)])
    assert code == 0


def test_cli_checkpoint_save_and_show(checkpoint_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """factory checkpoint --save persists state, then show reads it."""
    from factory.cli import main

    # Save
    code = main([
        "checkpoint", str(checkpoint_project),
        "--save",
        "--mode", "improve",
        "--experiment", "38",
        "--completed", "researcher,strategist",
        "--pending", "builder,reviewer",
        "--hypothesis", "Test hypothesis",
        "--scores", '{"tests": 0.9}',
    ])
    assert code == 0
    capsys.readouterr()  # clear output

    # Show
    code = main(["checkpoint", str(checkpoint_project)])
    assert code == 0
    output = capsys.readouterr().out
    assert "improve" in output
    assert "38" in output
    assert "researcher" in output
    assert "builder" in output


def test_cli_resume_no_checkpoint(checkpoint_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """factory resume <path> returns 1 when no checkpoint."""
    from factory.cli import main

    code = main(["resume", str(checkpoint_project)])
    assert code == 1
    output = capsys.readouterr().out
    assert "No checkpoint" in output


def test_cli_resume_with_checkpoint(checkpoint_project: Path, sample_state: CheckpointState, capsys: pytest.CaptureFixture[str]) -> None:
    """factory resume <path> displays resume context."""
    save_checkpoint(checkpoint_project, sample_state)

    from factory.cli import main

    code = main(["resume", str(checkpoint_project)])
    assert code == 0
    output = capsys.readouterr().out
    assert "Resume Context" in output
    assert "improve" in output
    assert "builder" in output
    assert "reviewer" in output
