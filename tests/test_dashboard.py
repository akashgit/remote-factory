"""Tests for factory.dashboard — API endpoints and project summary."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from factory.dashboard.app import (
    create_app,
    _load_experiment_dimensions,
    _load_latest_dimensions,
    _project_summary,
    _load_tsv,
)


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

    # Experiment directories with eval_after.json
    exp_001 = factory_a / "experiments" / "001"
    exp_001.mkdir(parents=True)
    (exp_001 / "eval_after.json").write_text(json.dumps({
        "total": 0.6,
        "results": [
            {"name": "tests_pass", "score": 0.8, "weight": 0.2, "passed": True,
             "details": "ok"},
            {"name": "lint_clean", "score": 0.5, "weight": 0.1, "passed": False,
             "details": "3 warnings"},
            {"name": "feature_completeness", "score": 0.7, "weight": 0.3,
             "passed": True, "details": "ok"},
        ],
        "guard_violations": [],
        "passed": True,
    }))

    exp_002 = factory_a / "experiments" / "002"
    exp_002.mkdir(parents=True)
    (exp_002 / "eval_after.json").write_text(json.dumps({
        "total": 0.55,
        "results": [
            {"name": "tests_pass", "score": 0.9, "weight": 0.2, "passed": True,
             "details": "ok"},
            {"name": "lint_clean", "score": 0.4, "weight": 0.1, "passed": False,
             "details": "5 warnings"},
            {"name": "feature_completeness", "score": 0.6, "weight": 0.3,
             "passed": True, "details": "ok"},
        ],
        "guard_violations": [],
        "passed": True,
    }))

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


class TestSummaryAPI:
    def test_summary_aggregation(self, client: TestClient):
        resp = client.get("/api/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_projects"] == 2
        assert data["active_projects"] == 0  # No recent events
        assert data["total_experiments"] == 2
        assert data["keep_count"] == 1
        assert data["revert_count"] == 1
        # avg_score: only proj-a has a score (0.55), proj-b has none
        assert data["avg_score"] == pytest.approx(0.55)
        # keep_rate: 1 / 2 = 0.5
        assert data["keep_rate"] == pytest.approx(0.5)

    def test_summary_empty_dir(self, tmp_path: Path):
        app = create_app(tmp_path)
        empty_client = TestClient(app)
        resp = empty_client.get("/api/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_projects"] == 0
        assert data["active_projects"] == 0
        assert data["avg_score"] is None
        assert data["total_experiments"] == 0
        assert data["keep_count"] == 0
        assert data["revert_count"] == 0
        assert data["keep_rate"] == 0

    def test_summary_keep_rate_calculation(self, projects_dir: Path):
        """Test keep rate with additional project data."""
        # Add another project with experiments
        proj_c = projects_dir / "proj-c"
        factory_c = proj_c / ".factory"
        factory_c.mkdir(parents=True)

        tsv = io.StringIO()
        writer = csv.writer(tsv, dialect="excel-tab")
        writer.writerow(["id", "timestamp", "hypothesis", "change_summary",
                         "issue_number", "pr_number", "score_before", "score_after",
                         "delta", "verdict", "cost_usd", "notes"])
        writer.writerow(["1", "2026-04-16T10:00:00", "h1", "s1",
                         "", "", "0.5", "0.7", "0.2", "keep", "0.1", ""])
        writer.writerow(["2", "2026-04-16T11:00:00", "h2", "s2",
                         "", "", "0.7", "0.8", "0.1", "keep", "0.1", ""])
        writer.writerow(["3", "2026-04-16T12:00:00", "h3", "s3",
                         "", "", "0.8", "0.75", "-0.05", "revert", "0.1", ""])
        (factory_c / "results.tsv").write_text(tsv.getvalue())

        app = create_app(projects_dir)
        c = TestClient(app)
        resp = c.get("/api/summary")
        data = resp.json()
        # proj-a: 2 exp (1 keep, 1 revert), proj-b: 0 exp, proj-c: 3 exp (2 keep, 1 revert)
        assert data["total_projects"] == 3
        assert data["total_experiments"] == 5
        assert data["keep_count"] == 3
        assert data["revert_count"] == 2
        assert data["keep_rate"] == pytest.approx(0.6)
        # avg_score: proj-a=0.55, proj-c=0.75 => (0.55+0.75)/2 = 0.65
        assert data["avg_score"] == pytest.approx(0.65)


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


class TestDimensionsAPI:
    def test_dimensions_endpoint(self, client: TestClient):
        resp = client.get("/api/projects/proj-a/dimensions")
        assert resp.status_code == 200
        data = resp.json()
        assert "dimensions" in data
        dims = data["dimensions"]
        assert len(dims) == 3
        # Should return the latest experiment (002)
        names = [d["name"] for d in dims]
        assert "tests_pass" in names
        assert "lint_clean" in names
        assert "feature_completeness" in names
        # Check latest values (from exp 002)
        tp = next(d for d in dims if d["name"] == "tests_pass")
        assert tp["score"] == 0.9
        assert tp["weight"] == 0.2
        assert tp["passed"] is True

    def test_dimensions_empty_project(self, client: TestClient):
        resp = client.get("/api/projects/proj-b/dimensions")
        assert resp.status_code == 200
        assert resp.json() == {"dimensions": []}

    def test_history_includes_score_fields(self, client: TestClient):
        resp = client.get("/api/projects/proj-a/history")
        rows = resp.json()
        assert len(rows) == 2
        # Both score_before and score_after should be present
        assert rows[0]["score_before"] == "0.500"
        assert rows[0]["score_after"] == "0.600"
        assert rows[1]["score_before"] == "0.600"
        assert rows[1]["score_after"] == "0.550"

    def test_history_includes_dimensions(self, client: TestClient):
        resp = client.get("/api/projects/proj-a/history")
        rows = resp.json()
        assert len(rows) == 2
        # Experiment 1 should have dimensions from eval_after.json
        assert "dimensions" in rows[0]
        assert len(rows[0]["dimensions"]) == 3
        # Experiment 2 as well
        assert len(rows[1]["dimensions"]) == 3


class TestProjectScores:
    def test_scores_in_project_summary(self, client: TestClient):
        resp = client.get("/api/projects")
        proj_a = next(p for p in resp.json() if p["name"] == "proj-a")
        assert "scores" in proj_a
        assert proj_a["scores"] == [0.6, 0.55]

    def test_scores_empty_project(self, client: TestClient):
        resp = client.get("/api/projects")
        proj_b = next(p for p in resp.json() if p["name"] == "proj-b")
        # scores field should not be present or be empty for empty project
        assert proj_b.get("scores") is None or proj_b.get("scores") == []


class TestDimensionHelpers:
    def test_load_experiment_dimensions(self, projects_dir: Path):
        dims = _load_experiment_dimensions(projects_dir / "proj-a", "1")
        assert len(dims) == 3
        assert dims[0]["name"] == "tests_pass"
        assert dims[0]["score"] == 0.8

    def test_load_experiment_dimensions_missing(self, projects_dir: Path):
        dims = _load_experiment_dimensions(projects_dir / "proj-a", "99")
        assert dims == []

    def test_load_experiment_dimensions_empty_id(self, projects_dir: Path):
        dims = _load_experiment_dimensions(projects_dir / "proj-a", "")
        assert dims == []

    def test_load_latest_dimensions(self, projects_dir: Path):
        dims = _load_latest_dimensions(projects_dir / "proj-a")
        assert len(dims) == 3
        # Latest is 002
        tp = next(d for d in dims if d["name"] == "tests_pass")
        assert tp["score"] == 0.9

    def test_load_latest_dimensions_empty(self, projects_dir: Path):
        dims = _load_latest_dimensions(projects_dir / "proj-b")
        assert dims == []


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
        assert args.projects_dir == "~/factory-projects"

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
