"""Tests for factory.checkpoint_hook — state reconstruction from disk artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.checkpoint import load_checkpoint
from factory.checkpoint_hook import reconstruct_and_save, reconstruct_state


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


def test_reconstruct_detects_ceo_verdict_files(factory_project: Path) -> None:
    """CEO verdict files (ceo-verdict-{role}.md) also indicate agent completion."""
    (factory_project / ".factory" / "reviews" / "ceo-verdict-researcher.md").write_text("PROCEED")
    (factory_project / ".factory" / "reviews" / "ceo-verdict-strategist.md").write_text("PROCEED")

    state = reconstruct_state(factory_project)
    assert "researcher" in state.completed_agents
    assert "strategist" in state.completed_agents
    assert "builder" not in state.completed_agents


def test_reconstruct_after_researcher_and_strategist(factory_project: Path) -> None:
    """After both researcher and strategist complete."""
    events_file = factory_project / ".factory" / "events.jsonl"
    events = [
        {"type": "agent.completed", "timestamp": "2026-04-29T10:02:00+00:00",
         "project": "test", "agent": "researcher", "data": {"return_code": 0}},
        {"type": "agent.completed", "timestamp": "2026-04-29T10:05:00+00:00",
         "project": "test", "agent": "strategist", "data": {"return_code": 0}},
    ]
    events_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    reviews = factory_project / ".factory" / "reviews"
    (reviews / "researcher-latest.md").write_text("done")
    (reviews / "strategist-latest.md").write_text("done")
    (factory_project / ".factory" / "strategy" / "current.md").write_text("strategy")

    state = reconstruct_state(factory_project)
    assert "researcher" in state.completed_agents
    assert "strategist" in state.completed_agents


def test_reconstruct_with_active_experiment(factory_project: Path) -> None:
    """Detects an active experiment (hypothesis without verdict)."""
    exp_dir = factory_project / ".factory" / "experiments" / "001"
    exp_dir.mkdir(parents=True)
    (exp_dir / "hypothesis.md").write_text("Add caching layer")

    state = reconstruct_state(factory_project)
    assert state.active_experiment_id == 1
    assert state.current_hypothesis == "Add caching layer"
    assert state.completed_hypotheses == []


def test_reconstruct_with_completed_experiment(factory_project: Path) -> None:
    """Detects completed experiments (have verdict.json)."""
    exp1 = factory_project / ".factory" / "experiments" / "001"
    exp1.mkdir(parents=True)
    (exp1 / "hypothesis.md").write_text("H1")
    (exp1 / "verdict.json").write_text(json.dumps({"verdict": "keep"}))

    exp2 = factory_project / ".factory" / "experiments" / "002"
    exp2.mkdir(parents=True)
    (exp2 / "hypothesis.md").write_text("H2 - active")

    state = reconstruct_state(factory_project)
    assert state.completed_hypotheses == [1]
    assert state.active_experiment_id == 2
    assert state.current_hypothesis == "H2 - active"


def test_reconstruct_with_eval_scores(factory_project: Path) -> None:
    """Extracts eval scores from events."""
    events_file = factory_project / ".factory" / "events.jsonl"
    events = [
        {"type": "eval.completed", "timestamp": "2026-04-29T10:10:00+00:00",
         "project": "test", "agent": None,
         "data": {"composite": 0.65, "passed": True, "dimensions": 11}},
    ]
    events_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    state = reconstruct_state(factory_project)
    assert state.last_eval_scores["composite"] == 0.65


def test_reconstruct_respects_cycle_boundary(factory_project: Path) -> None:
    """Only considers events after the last cycle.started."""
    events_file = factory_project / ".factory" / "events.jsonl"
    events = [
        {"type": "agent.completed", "timestamp": "2026-04-29T08:00:00+00:00",
         "project": "test", "agent": "researcher", "data": {"return_code": 0}},
        {"type": "agent.completed", "timestamp": "2026-04-29T08:05:00+00:00",
         "project": "test", "agent": "strategist", "data": {"return_code": 0}},
        {"type": "cycle.started", "timestamp": "2026-04-29T10:00:00+00:00",
         "project": "test", "agent": None, "data": {"cycle": 2}},
        {"type": "agent.completed", "timestamp": "2026-04-29T10:02:00+00:00",
         "project": "test", "agent": "researcher", "data": {"return_code": 0}},
    ]
    events_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    (factory_project / ".factory" / "reviews" / "researcher-latest.md").write_text("done")
    (factory_project / ".factory" / "reviews" / "strategist-latest.md").write_text("old")

    state = reconstruct_state(factory_project)
    assert "researcher" in state.completed_agents


def test_reconstruct_missing_events_file(factory_project: Path) -> None:
    """Handles missing events.jsonl gracefully."""
    (factory_project / ".factory" / "events.jsonl").unlink()
    state = reconstruct_state(factory_project)
    assert state.completed_agents == []


def test_reconstruct_corrupt_event_line(factory_project: Path) -> None:
    """Skips corrupt lines in events.jsonl."""
    events_file = factory_project / ".factory" / "events.jsonl"
    events_file.write_text(
        '{"type": "agent.completed", "agent": "researcher"}\n'
        "CORRUPT LINE\n"
        '{"type": "agent.completed", "agent": "strategist"}\n'
    )
    (factory_project / ".factory" / "reviews" / "researcher-latest.md").write_text("done")
    (factory_project / ".factory" / "reviews" / "strategist-latest.md").write_text("done")

    state = reconstruct_state(factory_project)
    assert "researcher" in state.completed_agents
    assert "strategist" in state.completed_agents


def test_reconstruct_and_save_writes_checkpoint(factory_project: Path) -> None:
    """reconstruct_and_save writes checkpoint.json that load_checkpoint can read."""
    events_file = factory_project / ".factory" / "events.jsonl"
    events_file.write_text(json.dumps(
        {"type": "agent.completed", "timestamp": "2026-04-29T10:02:00+00:00",
         "project": "test", "agent": "researcher", "data": {"return_code": 0}}
    ) + "\n")
    (factory_project / ".factory" / "reviews" / "researcher-latest.md").write_text("done")

    state = reconstruct_and_save(factory_project)
    assert state is not None
    assert "researcher" in state.completed_agents

    loaded = load_checkpoint(factory_project)
    assert loaded is not None
    assert "researcher" in loaded.completed_agents


def test_reconstruct_and_save_no_factory_dir(tmp_path: Path) -> None:
    """Returns None for projects without .factory/."""
    result = reconstruct_and_save(tmp_path / "nonexistent")
    assert result is None


def test_reconstruct_and_save_idempotent(factory_project: Path) -> None:
    """Calling twice produces identical checkpoints (except timestamp)."""
    reconstruct_and_save(factory_project)
    state1 = load_checkpoint(factory_project)

    reconstruct_and_save(factory_project)
    state2 = load_checkpoint(factory_project)

    assert state1 is not None
    assert state2 is not None
    assert state1.completed_agents == state2.completed_agents
    assert state1.active_experiment_id == state2.active_experiment_id
    assert state1.completed_hypotheses == state2.completed_hypotheses
