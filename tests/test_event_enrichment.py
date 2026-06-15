"""Tests for enriched lifecycle events (issue #556)."""

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from factory.events import emit_event, load_events


def _setup_factory_dir(project: Path) -> None:
    """Create minimal .factory directory for event emission."""
    factory_dir = project / ".factory"
    factory_dir.mkdir(parents=True, exist_ok=True)
    (factory_dir / "strategy").mkdir(parents=True, exist_ok=True)


# ── worktree events ──────────────────────────────────────────


@pytest.mark.real_worktree
def test_create_worktree_emits_event(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _setup_factory_dir(project)

    def fake_subprocess_run(cmd, **kwargs):
        if cmd[0] == "git" and "worktree" in cmd and "add" in cmd:
            wt_path = Path(cmd[3])
            wt_path.mkdir(parents=True, exist_ok=True)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        from factory.worktree import create_worktree
        create_worktree(project, "main")

    events = load_events(project)
    wt_events = [e for e in events if e["type"] == "worktree.created"]
    assert len(wt_events) == 1
    data = wt_events[0]["data"]
    assert "run_id" in data
    assert "worktree_path" in data
    assert data["branch"].startswith("factory/run-")
    assert data["base_branch"] == "main"


@pytest.mark.real_worktree
def test_remove_worktree_emits_event(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _setup_factory_dir(project)

    captured: list[dict] = []
    real_emit = emit_event

    def spy_emit(proj_path, event_type, *, agent=None, data=None):
        result = real_emit(proj_path, event_type, agent=agent, data=data)
        captured.append(result)
        return result

    wt_path = tmp_path / "fake-wt"

    with patch("factory.events.emit_event", side_effect=spy_emit), \
         patch("subprocess.run"):
        from factory.worktree import remove_worktree
        remove_worktree(project, wt_path, "factory/run-abc12345")

    events = load_events(project)
    rm_events = [e for e in events if e["type"] == "worktree.removed"]
    assert len(rm_events) == 1
    data = rm_events[0]["data"]
    assert data["run_id"] == "abc12345"
    assert data["branch"] == "factory/run-abc12345"


def test_worktree_created_event_schema(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _setup_factory_dir(project)

    event = emit_event(project, "worktree.created", data={
        "run_id": "abc12345",
        "worktree_path": str(project / ".factory" / "worktrees" / "run-abc12345"),
        "branch": "factory/run-abc12345",
        "base_branch": "main",
    })

    assert event["type"] == "worktree.created"
    assert event["data"]["run_id"] == "abc12345"
    assert event["data"]["branch"] == "factory/run-abc12345"
    assert event["data"]["base_branch"] == "main"
    assert "worktree_path" in event["data"]


def test_worktree_removed_event_schema(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _setup_factory_dir(project)

    event = emit_event(project, "worktree.removed", data={
        "run_id": "abc12345",
        "branch": "factory/run-abc12345",
    })

    assert event["type"] == "worktree.removed"
    assert event["data"]["run_id"] == "abc12345"
    assert event["data"]["branch"] == "factory/run-abc12345"


def test_worktree_events_persisted_to_jsonl(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _setup_factory_dir(project)

    emit_event(project, "worktree.created", data={
        "run_id": "dead0001",
        "worktree_path": "/tmp/wt",
        "branch": "factory/run-dead0001",
        "base_branch": "main",
    })
    emit_event(project, "worktree.removed", data={
        "run_id": "dead0001",
        "branch": "factory/run-dead0001",
    })

    events = load_events(project)
    types = [e["type"] for e in events]
    assert "worktree.created" in types
    assert "worktree.removed" in types


# ── experiment.finalize enrichment ────────────────────────────


def test_finalize_event_includes_enriched_fields(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _setup_factory_dir(project)

    event = emit_event(project, "experiment.finalize", data={
        "exp_id": 1,
        "verdict": "keep",
        "hypothesis": "test hypothesis",
        "pr_number": 42,
        "issue_number": 10,
        "score_before": 0.65,
        "score_after": 0.78,
        "delta": 0.13,
        "cost_usd": 1.23,
    })

    data = event["data"]
    assert data["pr_number"] == 42
    assert data["issue_number"] == 10
    assert data["score_before"] == 0.65
    assert data["score_after"] == 0.78
    assert data["delta"] == 0.13
    assert data["cost_usd"] == 1.23


def test_cmd_finalize_emits_enriched_event(tmp_path: Path) -> None:
    """cmd_finalize emits experiment.finalize with enriched fields."""
    project = tmp_path / "proj"
    project.mkdir()
    _setup_factory_dir(project)

    ns = argparse.Namespace(
        path=str(project),
        id=1,
        verdict="keep",
        hypothesis="Improve logging",
        summary="Added structlog",
        cost=2.50,
        issue=99,
        pr=55,
        score_before=0.60,
        score_after=0.75,
        notes="",
        force=True,
    )

    mock_store = MagicMock()

    with patch("factory.store.ExperimentStore", return_value=mock_store), \
         patch("factory.cli._run", return_value=None):
        from factory.cli import cmd_finalize
        cmd_finalize(ns)

    events = load_events(project)
    finalize_events = [e for e in events if e["type"] == "experiment.finalize"]
    assert len(finalize_events) == 1

    data = finalize_events[0]["data"]
    assert data["pr_number"] == 55
    assert data["issue_number"] == 99
    assert data["score_before"] == 0.60
    assert data["score_after"] == 0.75
    assert data["delta"] == 0.15
    assert data["cost_usd"] == 2.50


def test_finalize_event_with_null_scores(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _setup_factory_dir(project)

    ns = argparse.Namespace(
        path=str(project),
        id=2,
        verdict="revert",
        hypothesis="Bad idea",
        summary="Reverted",
        cost=None,
        issue=None,
        pr=None,
        score_before=None,
        score_after=None,
        notes="",
        force=True,
    )

    mock_store = MagicMock()

    with patch("factory.store.ExperimentStore", return_value=mock_store), \
         patch("factory.cli._run", return_value=None), \
         patch("factory.events.load_events", return_value=[]), \
         patch("factory.events.sum_agent_costs", return_value=0.0):
        from factory.cli import cmd_finalize
        cmd_finalize(ns)

    events_file = project / ".factory" / "events.jsonl"
    raw_events = []
    for line in events_file.read_text().splitlines():
        if line.strip():
            raw_events.append(json.loads(line))

    finalize_events = [e for e in raw_events if e["type"] == "experiment.finalize"]
    assert len(finalize_events) >= 1

    data = finalize_events[-1]["data"]
    assert data["pr_number"] is None
    assert data["issue_number"] is None
    assert data["score_before"] is None
    assert data["score_after"] is None
    assert data["delta"] is None


# ── backlog events ────────────────────────────────────────────


def test_backlog_add_emits_event(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _setup_factory_dir(project)

    ns = argparse.Namespace(
        path=str(project),
        item="Add dark mode support",
    )

    from factory.cli import cmd_backlog_add
    result = cmd_backlog_add(ns)

    assert result == 0

    events = load_events(project)
    add_events = [e for e in events if e["type"] == "backlog.added"]
    assert len(add_events) == 1
    assert add_events[0]["data"]["item"] == "Add dark mode support"


def test_backlog_remove_emits_event(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _setup_factory_dir(project)

    backlog_path = project / ".factory" / "strategy" / "backlog.md"
    backlog_path.write_text("- Fix login bug\n")

    ns = argparse.Namespace(
        path=str(project),
        item="Fix login bug",
    )

    from factory.cli import cmd_backlog_remove
    result = cmd_backlog_remove(ns)

    assert result == 0

    events = load_events(project)
    remove_events = [e for e in events if e["type"] == "backlog.removed"]
    assert len(remove_events) == 1
    assert remove_events[0]["data"]["item"] == "Fix login bug"


def test_backlog_add_duplicate_no_event(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _setup_factory_dir(project)

    backlog_path = project / ".factory" / "strategy" / "backlog.md"
    backlog_path.write_text("- Existing item\n")

    ns = argparse.Namespace(
        path=str(project),
        item="Existing item",
    )

    from factory.cli import cmd_backlog_add
    result = cmd_backlog_add(ns)

    assert result == 1

    events = load_events(project)
    add_events = [e for e in events if e["type"] == "backlog.added"]
    assert len(add_events) == 0


def test_backlog_remove_missing_no_event(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _setup_factory_dir(project)

    ns = argparse.Namespace(
        path=str(project),
        item="Nonexistent item",
    )

    from factory.cli import cmd_backlog_remove
    result = cmd_backlog_remove(ns)

    assert result == 1

    events = load_events(project)
    remove_events = [e for e in events if e["type"] == "backlog.removed"]
    assert len(remove_events) == 0
