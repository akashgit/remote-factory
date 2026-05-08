"""Tests for factory.events — event emission and loading."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from factory.events import discover_factory_projects, emit_event, load_events


class TestEmitEvent:
    def test_creates_events_jsonl(self, tmp_path):
        project = tmp_path / "myproject"
        project.mkdir()
        (project / ".factory").mkdir()

        emit_event(project, "agent.started", agent="builder", data={"task": "fix bug"})

        events_file = project / ".factory" / "events.jsonl"
        assert events_file.exists()

        lines = events_file.read_text().strip().splitlines()
        assert len(lines) == 1

        event = json.loads(lines[0])
        assert event["type"] == "agent.started"
        assert event["project"] == "myproject"
        assert event["agent"] == "builder"
        assert event["data"]["task"] == "fix bug"
        assert "timestamp" in event

    def test_appends_multiple_events(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        emit_event(project, "agent.started", agent="researcher")
        emit_event(project, "agent.completed", agent="researcher", data={"return_code": 0})
        emit_event(project, "agent.started", agent="builder")

        lines = (project / ".factory" / "events.jsonl").read_text().strip().splitlines()
        assert len(lines) == 3
        assert json.loads(lines[0])["type"] == "agent.started"
        assert json.loads(lines[1])["type"] == "agent.completed"
        assert json.loads(lines[2])["agent"] == "builder"

    def test_creates_factory_dir_if_missing(self, tmp_path):
        project = tmp_path / "newproj"
        project.mkdir()

        emit_event(project, "cycle.started")
        assert (project / ".factory" / "events.jsonl").exists()

    def test_returns_event_dict(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()

        result = emit_event(project, "test.event", agent="ceo", data={"x": 1})
        assert result["type"] == "test.event"
        assert result["agent"] == "ceo"
        assert result["data"]["x"] == 1

    def test_event_without_agent(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()

        event = emit_event(project, "cycle.started", data={"cycle": 1})
        assert event["agent"] is None
        assert event["data"]["cycle"] == 1


class TestLoadEvents:
    def test_loads_all_events(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        emit_event(project, "a")
        emit_event(project, "b")
        emit_event(project, "c")

        events = load_events(project)
        assert len(events) == 3
        assert [e["type"] for e in events] == ["a", "b", "c"]

    def test_returns_empty_for_missing_file(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        assert load_events(project) == []

    def test_filters_by_since(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        # Write two events with known timestamps
        events_file = project / ".factory" / "events.jsonl"
        old_event = {
            "type": "old",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "project": "proj",
            "agent": None,
            "data": {},
        }
        new_event = {
            "type": "new",
            "timestamp": "2026-04-17T12:00:00+00:00",
            "project": "proj",
            "agent": None,
            "data": {},
        }
        events_file.write_text(json.dumps(old_event) + "\n" + json.dumps(new_event) + "\n")

        # Filter: only events after 2026-03-01
        cutoff = datetime(2026, 3, 1, tzinfo=timezone.utc)
        events = load_events(project, since=cutoff)
        assert len(events) == 1
        assert events[0]["type"] == "new"

    def test_skips_blank_lines(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        events_file = project / ".factory" / "events.jsonl"
        event = json.dumps({
            "type": "x", "timestamp": "2026-01-01T00:00:00+00:00",
            "project": "proj", "agent": None, "data": {},
        })
        events_file.write_text(f"{event}\n\n{event}\n\n")

        events = load_events(project)
        assert len(events) == 2


class TestSymlinkResolution:
    def test_emit_event_resolves_symlinks(self, tmp_path):
        """Events go to the resolved path even when given a symlink."""
        real_dir = tmp_path / "real-project"
        real_dir.mkdir()
        (real_dir / ".factory").mkdir()

        symlink_dir = tmp_path / "link-project"
        symlink_dir.symlink_to(real_dir)

        # Emit via symlink path
        emit_event(symlink_dir, "test.event", data={"via": "symlink"})

        # Load via real path
        events = load_events(real_dir)
        assert len(events) == 1
        assert events[0]["type"] == "test.event"
        # The project name should be the resolved dir name, not the symlink name
        assert events[0]["project"] == "real-project"

        # Load via symlink path
        events2 = load_events(symlink_dir)
        assert len(events2) == 1


class TestDiscoverFactoryProjects:
    def test_finds_projects_with_factory_dir(self, tmp_path):
        (tmp_path / "proj-a" / ".factory").mkdir(parents=True)
        (tmp_path / "proj-b" / ".factory").mkdir(parents=True)
        (tmp_path / "not-factory").mkdir()

        projects = discover_factory_projects(tmp_path)
        names = [p.name for p in projects]
        assert "proj-a" in names
        assert "proj-b" in names
        assert "not-factory" not in names

    def test_returns_empty_for_missing_dir(self, tmp_path):
        assert discover_factory_projects(tmp_path / "nonexistent") == []

    def test_returns_sorted(self, tmp_path):
        (tmp_path / "zeta" / ".factory").mkdir(parents=True)
        (tmp_path / "alpha" / ".factory").mkdir(parents=True)

        projects = discover_factory_projects(tmp_path)
        assert projects[0].name == "alpha"
        assert projects[1].name == "zeta"
