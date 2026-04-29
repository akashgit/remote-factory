"""Tests for factory.checkpoint_hook — state reconstruction from disk artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.checkpoint_hook import reconstruct_state


@pytest.fixture
def factory_project(tmp_path: Path) -> Path:
    """Create a minimal .factory/ directory with events.jsonl."""
    project = tmp_path / "test-project"
    project.mkdir()
    factory = project / ".factory"
    factory.mkdir()
    (factory / "experiments").mkdir()
    (factory / "strategy").mkdir()
    (factory / "reviews").mkdir()
    (factory / "events.jsonl").write_text("")
    (factory / "config.json").write_text(json.dumps({
        "project_name": "test",
        "description": "test project",
        "eval_command": "echo ok",
        "language": "python",
        "framework": None,
    }))
    return project


def test_reconstruct_empty_state(factory_project: Path) -> None:
    """With no events, reconstruction returns a minimal checkpoint."""
    state = reconstruct_state(factory_project)
    assert state.mode == "improve"
    assert state.completed_agents == []
    assert state.pending_agents == []
    assert state.active_experiment_id is None
    assert state.completed_hypotheses == []


def test_reconstruct_after_researcher(factory_project: Path) -> None:
    """After researcher completes, it appears in completed_agents."""
    events_file = factory_project / ".factory" / "events.jsonl"
    events = [
        {"type": "agent.started", "timestamp": "2026-04-29T10:00:00+00:00",
         "project": "test", "agent": "ceo", "data": {}},
        {"type": "agent.started", "timestamp": "2026-04-29T10:01:00+00:00",
         "project": "test", "agent": "researcher", "data": {}},
        {"type": "agent.completed", "timestamp": "2026-04-29T10:02:00+00:00",
         "project": "test", "agent": "researcher", "data": {"return_code": 0}},
    ]
    events_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    (factory_project / ".factory" / "reviews" / "researcher-latest.md").write_text("research done")

    state = reconstruct_state(factory_project)
    assert "researcher" in state.completed_agents
    assert state.active_experiment_id is None
