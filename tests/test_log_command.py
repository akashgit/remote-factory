"""Tests for the factory log CLI command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def log_project(tmp_path: Path) -> Path:
    """Create a minimal project with .factory/."""
    project = tmp_path / "log-project"
    project.mkdir()
    (project / ".factory").mkdir()
    return project


def test_log_appends_event(log_project: Path) -> None:
    """factory log appends an event to events.jsonl."""
    from factory.cli import build_parser, cmd_log

    parser = build_parser()
    args = parser.parse_args([
        "log", str(log_project), "phase.research.completed",
        "--data", '{"verdict": "PROCEED"}',
    ])
    code = cmd_log(args)
    assert code == 0

    events_file = log_project / ".factory" / "events.jsonl"
    assert events_file.exists()
    events = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
    assert len(events) == 1
    assert events[0]["type"] == "phase.research.completed"
    assert events[0]["data"]["verdict"] == "PROCEED"


def test_log_without_data(log_project: Path) -> None:
    """factory log works without --data flag."""
    from factory.cli import build_parser, cmd_log

    parser = build_parser()
    args = parser.parse_args(["log", str(log_project), "sprint.started"])
    code = cmd_log(args)
    assert code == 0

    events_file = log_project / ".factory" / "events.jsonl"
    events = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
    assert len(events) == 1
    assert events[0]["type"] == "sprint.started"
    assert events[0]["data"] == {}


def test_log_invalid_json_data(log_project: Path) -> None:
    """factory log rejects invalid JSON in --data."""
    from factory.cli import build_parser, cmd_log

    parser = build_parser()
    args = parser.parse_args([
        "log", str(log_project), "test.event", "--data", "not-json",
    ])
    code = cmd_log(args)
    assert code == 1


def test_log_with_agent_flag(log_project: Path) -> None:
    """factory log --agent sets the agent field in the event."""
    from factory.cli import build_parser, cmd_log

    parser = build_parser()
    args = parser.parse_args([
        "log", str(log_project), "phase.research.completed",
        "--agent", "ceo",
    ])
    code = cmd_log(args)
    assert code == 0

    events_file = log_project / ".factory" / "events.jsonl"
    events = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
    assert len(events) == 1
    assert events[0]["agent"] == "ceo"
