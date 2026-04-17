"""Tests for factory.dashboard — API endpoints and project summary."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from factory.dashboard.app import create_app, _project_summary, _load_tsv


@pytest.fixture()
def projects_dir(tmp_path: Path) -> Path:
    """Create a projects directory with two factory-managed projects."""
    # Project A: has experiments
    proj_a = tmp_path / "proj-a"
    factory_a = proj_a / ".factory"
    factory_a.mkdir(parents=True)

    # Config
    (factory_a / "config.json").write_text(json.dumps({
        "goal": "Improve test coverage",
        "scope": ["src/"],
        "guards": [],
        "eval_command": "pytest",
        "eval_threshold": 0.5,
        "constraints": [],
    }))

    # Results TSV
    tsv = io.StringIO()
    writer = csv.writer(tsv, dialect="excel-tab")
    writer.writerow(["id", "timestamp", "hypothesis", "change_summary",
                     "issue_number", "pr_number", "score_before", "score_after",
                     "delta", "verdict", "cost_usd", "notes"])
    writer.writerow(["1", "2026-04-16T10:00:00", "Add unit tests", "Added 5 tests",
                     "", "", "0.500", "0.600", "0.100", "keep", "0.50", ""])
    writer.writerow(["2", "2026-04-17T10:00:00", "Fix linting", "Ran ruff",
                     "", "", "0.600", "0.550", "-0.050", "revert", "0.30", ""])
    (factory_a / "results.tsv").write_text(tsv.getvalue())

    # Project B: empty factory
    proj_b = tmp_path / "proj-b"
    (proj_b / ".factory").mkdir(parents=True)

    return tmp_path


@pytest.fixture()
def client(projects_dir: Path) -> TestClient:
    app = create_app(projects_dir)
    return TestClient(app)


class TestDashboardAPI:
    def test_index_returns_html(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Factory" in resp.text

    def test_list_projects(self, client: TestClient):
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        projects = resp.json()
        names = [p["name"] for p in projects]
        assert "proj-a" in names
        assert "proj-b" in names

    def test_project_has_summary_fields(self, client: TestClient):
        resp = client.get("/api/projects")
        proj_a = next(p for p in resp.json() if p["name"] == "proj-a")
        assert proj_a["experiment_count"] == 2
        assert proj_a["keep_count"] == 1
        assert proj_a["revert_count"] == 1
        assert proj_a["goal"] == "Improve test coverage"
        assert proj_a["latest_score"] == 0.55

    def test_project_history(self, client: TestClient):
        resp = client.get("/api/projects/proj-a/history")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 2
        assert rows[0]["hypothesis"] == "Add unit tests"
        assert rows[1]["verdict"] == "revert"

    def test_project_history_empty(self, client: TestClient):
        resp = client.get("/api/projects/proj-b/history")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_project_events_empty(self, client: TestClient):
        resp = client.get("/api/projects/proj-a/events")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_project_events_with_data(self, client: TestClient, projects_dir: Path):
        from factory.events import emit_event

        proj_a = projects_dir / "proj-a"
        emit_event(proj_a, "agent.started", agent="builder")
        emit_event(proj_a, "agent.completed", agent="builder", data={"return_code": 0})

        resp = client.get("/api/projects/proj-a/events")
        events = resp.json()
        assert len(events) == 2
        assert events[0]["type"] == "agent.started"

    def test_project_events_limit(self, client: TestClient, projects_dir: Path):
        from factory.events import emit_event

        proj_a = projects_dir / "proj-a"
        for i in range(10):
            emit_event(proj_a, f"event.{i}")

        resp = client.get("/api/projects/proj-a/events?limit=3")
        events = resp.json()
        assert len(events) == 3
        # Should return the last 3
        assert events[0]["type"] == "event.7"


class TestProjectSummary:
    def test_basic_summary(self, projects_dir: Path):
        info = _project_summary(projects_dir / "proj-a")
        assert info["name"] == "proj-a"
        assert info["experiment_count"] == 2
        assert info["keep_count"] == 1
        assert info["revert_count"] == 1
        assert info["latest_score"] == 0.55
        assert info["goal"] == "Improve test coverage"

    def test_empty_project(self, projects_dir: Path):
        info = _project_summary(projects_dir / "proj-b")
        assert info["name"] == "proj-b"
        assert info["experiment_count"] == 0
        assert info["latest_score"] is None

    def test_last_experiment(self, projects_dir: Path):
        info = _project_summary(projects_dir / "proj-a")
        last = info["last_experiment"]
        assert last is not None
        assert last["id"] == "2"
        assert last["verdict"] == "revert"


class TestLoadTsv:
    def test_loads_tsv(self, tmp_path: Path):
        tsv_file = tmp_path / "test.tsv"
        buf = io.StringIO()
        writer = csv.writer(buf, dialect="excel-tab")
        writer.writerow(["name", "value"])
        writer.writerow(["alpha", "1"])
        writer.writerow(["beta", "2"])
        tsv_file.write_text(buf.getvalue())

        rows = _load_tsv(tsv_file)
        assert len(rows) == 2
        assert rows[0]["name"] == "alpha"
        assert rows[1]["value"] == "2"


class TestDashboardCLI:
    def test_dashboard_parser_exists(self):
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["dashboard"])
        assert args.command == "dashboard"

    def test_dashboard_parser_defaults(self):
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["dashboard"])
        assert args.port == 8420
        assert args.host == "0.0.0.0"
        assert args.projects_dir == "~/cursor-projects"

    def test_dashboard_parser_custom(self):
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "dashboard",
            "--port", "9000",
            "--host", "127.0.0.1",
            "--projects-dir", "/tmp/projects",
        ])
        assert args.port == 9000
        assert args.host == "127.0.0.1"
        assert args.projects_dir == "/tmp/projects"


class TestBanner:
    def test_banner_function_exists(self):
        from factory.cli import _print_banner
        # Should not raise
        _print_banner("improve")

    def test_banner_no_color(self, monkeypatch, capsys):
        monkeypatch.setenv("NO_COLOR", "1")
        from factory.cli import _print_banner
        _print_banner("meta")
        captured = capsys.readouterr()
        assert "Factory v2" in captured.err
        assert "meta" in captured.err
