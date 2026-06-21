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
    _load_research_runs,
    _parse_diff_stats,
    _parse_failure_categories,
    _parse_single_verdict,
    _project_summary,
    _load_tsv,
    _read_json_safe,
    _read_text_safe,
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
        assert "min-height: 0" in resp.text

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


# ── Phase Detail Tests ──


@pytest.fixture()
def phase_projects_dir(tmp_path: Path) -> Path:
    """Projects dir with full .factory/ artifacts for phase-detail tests."""
    proj = tmp_path / "proj-phase"
    factory = proj / ".factory"

    # Create all subdirs
    for subdir in ["experiments/001", "experiments/002", "strategy", "reviews"]:
        (factory / subdir).mkdir(parents=True)

    # Config
    (factory / "config.json").write_text(json.dumps({
        "goal": "Build a weather CLI",
        "scope": ["src/"],
        "guards": ["Do not delete tests"],
        "eval_command": "python eval/score.py",
        "eval_threshold": 0.74,
        "constraints": ["Prefer small changes"],
    }))

    # Eval profile
    (factory / "eval_profile.json").write_text(json.dumps({
        "project_type": "cli_tool",
        "confidence": 0.85,
        "human_reviewed": True,
        "dimensions": [
            {"name": "tests", "weight": 0.4, "source": "discovered",
             "description": "Run test suite", "command": "pytest"},
            {"name": "lint", "weight": 0.3, "source": "discovered",
             "description": "Lint check", "command": "ruff check ."},
        ],
    }))

    # Strategy files
    (factory / "strategy" / "research.md").write_text(
        "# Research\nThe project uses argparse for CLI."
    )
    (factory / "strategy" / "observations.md").write_text(
        "# Observations\n- No structured logging\n- 72% test coverage"
    )
    (factory / "strategy" / "current.md").write_text(
        "## H1: Add structlog\nInstrument all modules with structlog."
    )
    (factory / "strategy" / "backlog.md").write_text(
        "- Add retry logic\n- Improve error messages"
    )

    # Agent outputs
    (factory / "reviews" / "researcher-latest.md").write_text("Researcher output here")
    (factory / "reviews" / "strategist-latest.md").write_text("Strategist output here")
    (factory / "reviews" / "builder-latest.md").write_text("Builder output here")
    (factory / "reviews" / "qa-latest.md").write_text("QA output here")
    (factory / "reviews" / "archivist-latest.md").write_text("Archivist output here")
    (factory / "reviews" / "session-summary.md").write_text("# Session Summary\nAll good.")

    # CEO verdicts
    (factory / "reviews" / "ceo-verdict-researcher.md").write_text(
        "**Verdict:** PROCEED\n**Rationale:** Research is thorough\n"
        "**Issues found:** None"
    )
    (factory / "reviews" / "ceo-verdict-strategist.md").write_text(
        "**Verdict:** PROCEED\n**Rationale:** Strategy approved\n"
        "**Issues found:** None"
    )
    (factory / "reviews" / "ceo-verdict-builder.md").write_text(
        "**Verdict:** REDIRECT\n**Rationale:** Missing test coverage\n"
        "**Issues found:**\n- No tests for new module\n- Unused import"
    )
    (factory / "reviews" / "ceo-verdict-qa.md").write_text(
        "**Verdict:** ABORT\n**Rationale:** Critical regression\n"
        "**Issues found:** None"
    )

    # Experiment artifacts
    (factory / "experiments" / "001" / "hypothesis.md").write_text(
        "Add structlog to all modules"
    )
    (factory / "experiments" / "001" / "eval_before.json").write_text(json.dumps({
        "total": 0.65, "results": [
            {"name": "tests", "score": 0.8, "weight": 0.4, "passed": True},
            {"name": "lint", "score": 0.5, "weight": 0.3, "passed": False},
        ],
    }))
    (factory / "experiments" / "001" / "eval_after.json").write_text(json.dumps({
        "total": 0.78, "results": [
            {"name": "tests", "score": 0.9, "weight": 0.4, "passed": True},
            {"name": "lint", "score": 0.7, "weight": 0.3, "passed": True},
        ],
    }))
    (factory / "experiments" / "001" / "changes.diff").write_text(
        "diff --git a/src/main.py b/src/main.py\n"
        "--- a/src/main.py\n"
        "+++ b/src/main.py\n"
        "@@ -1,3 +1,5 @@\n"
        "+import structlog\n"
        "+log = structlog.get_logger()\n"
        " def main():\n"
        "-    print('hello')\n"
        "+    log.info('hello')\n"
    )

    # Results TSV
    tsv = io.StringIO()
    writer = csv.writer(tsv, dialect="excel-tab")
    writer.writerow(["id", "timestamp", "hypothesis", "change_summary",
                     "issue_number", "pr_number", "score_before", "score_after",
                     "delta", "verdict", "cost_usd", "notes"])
    writer.writerow(["1", "2026-05-01T10:00:00", "Add structlog", "Added logging",
                     "", "", "0.650", "0.780", "0.130", "keep", "0.40", ""])
    (factory / "results.tsv").write_text(tsv.getvalue())

    # Performance report
    (factory / "performance_report.json").write_text(json.dumps({
        "project_name": "proj-phase",
        "total_experiments": 1,
        "keep_count": 1,
        "revert_count": 0,
        "keep_rate": 1.0,
        "latest_score": 0.78,
    }))

    # Events — set current phase to Eval (so Research/Strategize/Build are completed)
    from factory.events import emit_event
    emit_event(proj, "cycle.started", data={"mode": "Improve"})
    emit_event(proj, "detect", data={"state": "running"})
    emit_event(proj, "agent.started", agent="researcher", data={"task": "analyze"})
    emit_event(proj, "agent.completed", agent="researcher", data={"return_code": 0})
    emit_event(proj, "agent.started", agent="strategist", data={"task": "plan"})
    emit_event(proj, "agent.completed", agent="strategist", data={"return_code": 0})
    emit_event(proj, "experiment.begin", data={"exp_id": 1, "hypothesis": "Add structlog"})
    emit_event(proj, "agent.started", agent="builder", data={"task": "implement"})
    emit_event(proj, "agent.completed", agent="builder", data={"return_code": 0})
    emit_event(proj, "agent.started", agent="qa", data={"task": "verify"})
    emit_event(proj, "agent.completed", agent="qa", data={"return_code": 0})
    emit_event(proj, "eval.started", data={"command": "python eval/score.py"})
    emit_event(proj, "eval.completed", data={"composite": 0.78, "passed": True, "dimensions": 2})

    return tmp_path


@pytest.fixture()
def phase_client(phase_projects_dir: Path) -> TestClient:
    app = create_app(phase_projects_dir)
    return TestClient(app)


class TestPhaseDetailHelpers:
    def test_read_text_safe_exists(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        assert _read_text_safe(f) == "hello"

    def test_read_text_safe_missing(self, tmp_path: Path):
        assert _read_text_safe(tmp_path / "nope.txt") is None

    def test_read_json_safe_exists(self, tmp_path: Path):
        f = tmp_path / "test.json"
        f.write_text('{"key": "val"}')
        assert _read_json_safe(f) == {"key": "val"}

    def test_read_json_safe_invalid(self, tmp_path: Path):
        f = tmp_path / "bad.json"
        f.write_text("not json")
        assert _read_json_safe(f) is None

    def test_parse_single_verdict_proceed(self, tmp_path: Path):
        f = tmp_path / "verdict.md"
        f.write_text(
            "**Verdict:** PROCEED\n**Rationale:** Looks good\n"
            "**Issues found:** None"
        )
        v = _parse_single_verdict(f)
        assert v is not None
        assert v["decision"] == "PROCEED"
        assert v["rationale"] == "Looks good"
        assert v["issues"] == []

    def test_parse_single_verdict_with_issues(self, tmp_path: Path):
        f = tmp_path / "verdict.md"
        f.write_text(
            "**Verdict:** REDIRECT\n**Rationale:** Needs work\n"
            "**Issues found:**\n- Missing tests\n- Unused import"
        )
        v = _parse_single_verdict(f)
        assert v is not None
        assert v["decision"] == "REDIRECT"
        assert v["issues"] == ["Missing tests", "Unused import"]

    def test_parse_single_verdict_missing(self, tmp_path: Path):
        assert _parse_single_verdict(tmp_path / "nope.md") is None

    def test_parse_single_verdict_no_match(self, tmp_path: Path):
        f = tmp_path / "verdict.md"
        f.write_text("Just some notes, no verdict here.")
        assert _parse_single_verdict(f) is None

    def test_parse_diff_stats(self):
        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "+added line 1\n"
            "+added line 2\n"
            "-removed line\n"
            "diff --git a/bar.py b/bar.py\n"
            "+another add\n"
        )
        stats = _parse_diff_stats(diff)
        assert stats["files_changed"] == 2
        assert stats["insertions"] == 3
        assert stats["deletions"] == 1


class TestPhaseDetailAPI:
    def test_invalid_phase_returns_400(self, phase_client: TestClient):
        resp = phase_client.get("/api/projects/proj-phase/phase-detail/Invalid")
        assert resp.status_code == 400
        assert "Invalid phase" in resp.json()["error"]

    def test_detect_phase(self, phase_client: TestClient):
        resp = phase_client.get("/api/projects/proj-phase/phase-detail/Detect")
        assert resp.status_code == 200
        body = resp.json()
        assert body["phase"] == "Detect"
        assert body["status"] == "completed"
        data = body["data"]
        assert data["goal"] == "Build a weather CLI"
        assert data["eval_threshold"] == 0.74
        assert data["dimensions_count"] == 2
        assert body["verdict"] is None

    def test_discover_phase(self, phase_client: TestClient):
        resp = phase_client.get("/api/projects/proj-phase/phase-detail/Discover")
        body = resp.json()
        assert body["status"] == "completed"
        data = body["data"]
        assert data["project_type"] == "cli_tool"
        assert data["human_reviewed"] is True
        assert len(data["dimensions"]) == 2
        assert data["dimensions"][0]["name"] == "tests"

    def test_research_phase(self, phase_client: TestClient):
        resp = phase_client.get("/api/projects/proj-phase/phase-detail/Research")
        body = resp.json()
        assert body["status"] == "completed"
        data = body["data"]
        assert "argparse" in data["research"]
        assert "72% test coverage" in data["observations"]
        assert data["agent_output"] == "Researcher output here"
        verdict = body["verdict"]
        assert verdict["decision"] == "PROCEED"
        assert verdict["rationale"] == "Research is thorough"

    def test_strategize_phase(self, phase_client: TestClient):
        resp = phase_client.get("/api/projects/proj-phase/phase-detail/Strategize")
        body = resp.json()
        assert body["status"] == "completed"
        data = body["data"]
        assert "H1" in data["strategy"]
        assert "retry logic" in data["backlog"]
        verdict = body["verdict"]
        assert verdict["decision"] == "PROCEED"

    def test_build_phase(self, phase_client: TestClient):
        resp = phase_client.get("/api/projects/proj-phase/phase-detail/Build")
        body = resp.json()
        assert body["status"] == "completed"
        data = body["data"]
        assert data["experiment_id"] == 1
        assert "structlog" in data["hypothesis"]
        assert "diff --git" in data["diff"]
        assert data["diff_stats"]["files_changed"] == 1
        assert data["diff_stats"]["insertions"] == 3
        assert data["diff_stats"]["deletions"] == 1
        verdict = body["verdict"]
        assert verdict["decision"] == "REDIRECT"
        assert len(verdict["issues"]) == 2

    def test_review_phase(self, phase_client: TestClient):
        resp = phase_client.get("/api/projects/proj-phase/phase-detail/Review")
        body = resp.json()
        assert body["status"] == "completed"
        data = body["data"]
        assert data["agent_output"] == "QA output here"
        verdict = body["verdict"]
        assert verdict["decision"] == "ABORT"

    def test_eval_phase(self, phase_client: TestClient):
        resp = phase_client.get("/api/projects/proj-phase/phase-detail/Eval")
        body = resp.json()
        assert body["phase"] == "Eval"
        assert body["status"] == "active"
        data = body["data"]
        assert data["experiment_id"] == 1
        assert data["score_before"]["total"] == 0.65
        assert data["score_after"]["total"] == 0.78
        assert data["delta"] == pytest.approx(0.13)

    def test_archive_phase_is_future(self, phase_client: TestClient):
        resp = phase_client.get("/api/projects/proj-phase/phase-detail/Archive")
        body = resp.json()
        assert body["status"] == "future"
        assert body["data"] is None

    def test_missing_files_graceful(self, phase_projects_dir: Path):
        """Phase detail with no artifacts should return empty strings, not crash."""
        # proj-phase has events but let's test a project with no strategy files
        proj_empty = phase_projects_dir / "proj-empty"
        factory_empty = proj_empty / ".factory"
        factory_empty.mkdir(parents=True)
        (factory_empty / "config.json").write_text('{"goal":"test"}')

        from factory.events import emit_event
        emit_event(proj_empty, "agent.started", agent="researcher")
        emit_event(proj_empty, "agent.completed", agent="researcher")

        app2 = create_app(phase_projects_dir)
        c2 = TestClient(app2)
        resp = c2.get("/api/projects/proj-empty/phase-detail/Research")
        body = resp.json()
        assert body["status"] == "active"
        assert body["data"]["research"] == ""
        assert body["data"]["observations"] == ""
        assert body["verdict"] is None


class TestModeAwarePhaseDetail:
    def test_state_includes_phases_list(self, phase_client: TestClient):
        resp = phase_client.get("/api/projects/proj-phase/state")
        assert resp.status_code == 200
        state = resp.json()
        assert "phases" in state
        assert isinstance(state["phases"], list)
        assert len(state["phases"]) > 0
        assert "hypothesis_number" in state
        assert "loop_phases" in state

    def test_mode_specific_phase_accepted(self, phase_projects_dir: Path):
        """Improve mode phase names should be accepted by phase-detail."""
        proj = phase_projects_dir / "proj-improve"
        factory = proj / ".factory"
        factory.mkdir(parents=True)
        (factory / "config.json").write_text('{"goal":"test"}')
        (factory / "strategy" / "research.md").parents[0].mkdir(exist_ok=True)
        (factory / "strategy" / "research.md").write_text("# Research\nFindings here.")
        (factory / "reviews").mkdir(exist_ok=True)
        (factory / "reviews" / "researcher-latest.md").write_text("Output")

        from factory.events import emit_event
        emit_event(proj, "cycle.started", data={"mode": "improve"})
        emit_event(proj, "agent.started", agent="researcher", data={"task": "study"})
        emit_event(proj, "agent.completed", agent="researcher")
        emit_event(proj, "agent.started", agent="strategist", data={"task": "plan"})

        app = create_app(phase_projects_dir)
        c = TestClient(app)

        resp = c.get("/api/projects/proj-improve/phase-detail/Observe")
        assert resp.status_code == 200
        body = resp.json()
        assert body["phase"] == "Observe"
        assert body["status"] == "completed"
        assert "Findings here" in body["data"]["research"]

    def test_generic_phase_still_accepted(self, phase_client: TestClient):
        """Generic phase names (backward compat) should not return 400."""
        resp = phase_client.get("/api/projects/proj-phase/phase-detail/Research")
        assert resp.status_code == 200


# ── Research Dashboard Tests ──


@pytest.fixture()
def research_projects_dir(tmp_path: Path) -> Path:
    """Create a project with .factory/research/runs/ data for research view tests."""
    proj = tmp_path / "proj-research"
    runs_dir = proj / ".factory" / "research" / "runs"

    # Baseline cycle
    baseline = runs_dir / "000-baseline"
    baseline.mkdir(parents=True)
    (baseline / "summary.json").write_text(json.dumps({
        "metric_value": 0.45,
        "status": "completed",
        "duration": 120,
    }))

    # Cycle 1 — score improves
    cycle_1 = runs_dir / "cycle-001"
    cycle_1.mkdir()
    (cycle_1 / "summary.json").write_text(json.dumps({
        "metric_value": 0.52,
        "status": "completed",
        "duration": 95,
    }))
    (cycle_1 / "failure_analysis.md").write_text(
        "- timeout: 3\n- assertion_error: 2\n"
    )

    # Cycle 2 — score dips
    cycle_2 = runs_dir / "cycle-002"
    cycle_2.mkdir()
    (cycle_2 / "summary.json").write_text(json.dumps({
        "metric_value": 0.48,
        "status": "completed",
        "duration": 110,
    }))
    (cycle_2 / "failure_analysis.md").write_text(
        "- timeout: 1\n- connection_error: 4\n"
    )

    # Cycle 3 — score recovers to new high
    cycle_3 = runs_dir / "cycle-003"
    cycle_3.mkdir()
    (cycle_3 / "summary.json").write_text(json.dumps({
        "metric_value": 0.60,
        "status": "completed",
        "duration": 80,
    }))

    # Empty project (no research data)
    empty_proj = tmp_path / "proj-empty"
    (empty_proj / ".factory").mkdir(parents=True)

    return tmp_path


@pytest.fixture()
def research_client(research_projects_dir: Path) -> TestClient:
    app = create_app(research_projects_dir)
    return TestClient(app)


class TestParseFailureCategories:
    def test_basic_parsing(self):
        text = "- timeout: 3\n- assertion_error: 2\n"
        cats = _parse_failure_categories(text)
        assert cats == {"timeout": 3, "assertion_error": 2}

    def test_bold_categories(self):
        text = "- **timeout**: 5\n- **parse_error**: 1\n"
        cats = _parse_failure_categories(text)
        assert cats == {"timeout": 5, "parse_error": 1}

    def test_asterisk_bullets(self):
        text = "* timeout: 2\n* flaky: 1\n"
        cats = _parse_failure_categories(text)
        assert cats == {"timeout": 2, "flaky": 1}

    def test_ignores_non_matching_lines(self):
        text = "# Failure Analysis\n\nSome description.\n\n- timeout: 3\n"
        cats = _parse_failure_categories(text)
        assert cats == {"timeout": 3}

    def test_empty_text(self):
        assert _parse_failure_categories("") == {}


class TestLoadResearchRuns:
    def test_loads_all_cycles(self, research_projects_dir: Path):
        factory_dir = research_projects_dir / "proj-research" / ".factory"
        data = _load_research_runs(factory_dir)
        assert len(data["cycles"]) == 4

    def test_cycle_fields(self, research_projects_dir: Path):
        factory_dir = research_projects_dir / "proj-research" / ".factory"
        data = _load_research_runs(factory_dir)
        baseline = data["cycles"][0]
        assert baseline["name"] == "000-baseline"
        assert baseline["metric_value"] == 0.45
        assert baseline["status"] == "completed"
        assert baseline["duration"] == 120
        assert baseline["delta"] is None
        assert baseline["dominant_failure"] is None

    def test_delta_calculation(self, research_projects_dir: Path):
        factory_dir = research_projects_dir / "proj-research" / ".factory"
        data = _load_research_runs(factory_dir)
        cycle_1 = data["cycles"][1]
        assert cycle_1["delta"] == pytest.approx(0.07)
        cycle_2 = data["cycles"][2]
        assert cycle_2["delta"] == pytest.approx(-0.04)

    def test_dominant_failure(self, research_projects_dir: Path):
        factory_dir = research_projects_dir / "proj-research" / ".factory"
        data = _load_research_runs(factory_dir)
        assert data["cycles"][1]["dominant_failure"] == "timeout"
        assert data["cycles"][2]["dominant_failure"] == "connection_error"
        assert data["cycles"][3]["dominant_failure"] is None

    def test_failure_distribution_aggregated(self, research_projects_dir: Path):
        factory_dir = research_projects_dir / "proj-research" / ".factory"
        data = _load_research_runs(factory_dir)
        dist = data["failure_distribution"]
        assert dist["timeout"] == 4
        assert dist["assertion_error"] == 2
        assert dist["connection_error"] == 4

    def test_ratchet_monotonic(self, research_projects_dir: Path):
        factory_dir = research_projects_dir / "proj-research" / ".factory"
        data = _load_research_runs(factory_dir)
        ratchet = data["ratchet"]
        assert ratchet["labels"] == ["000-baseline", "cycle-001", "cycle-002", "cycle-003"]
        assert ratchet["scores"] == [0.45, 0.52, 0.48, 0.60]
        assert ratchet["best"] == [0.45, 0.52, 0.52, 0.60]
        for i in range(1, len(ratchet["best"])):
            assert ratchet["best"][i] >= ratchet["best"][i - 1]

    def test_no_research_dir(self, research_projects_dir: Path):
        factory_dir = research_projects_dir / "proj-empty" / ".factory"
        data = _load_research_runs(factory_dir)
        assert data["cycles"] == []
        assert data["failure_distribution"] == {}
        assert data["ratchet"] == {"labels": [], "scores": [], "best": []}

    def test_skips_dirs_without_summary(self, tmp_path: Path):
        runs_dir = tmp_path / ".factory" / "research" / "runs" / "no-summary"
        runs_dir.mkdir(parents=True)
        data = _load_research_runs(tmp_path / ".factory")
        assert data["cycles"] == []


class TestResearchDashboardAPI:
    def test_research_runs_endpoint(self, research_client: TestClient):
        resp = research_client.get("/api/projects/proj-research/research-runs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["cycles"]) == 4
        assert "failure_distribution" in data
        assert "ratchet" in data

    def test_research_runs_no_data(self, research_client: TestClient):
        resp = research_client.get("/api/projects/proj-empty/research-runs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cycles"] == []

    def test_research_view_returns_html(self, research_client: TestClient):
        resp = research_client.get("/research/proj-research")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "failure-chart" in resp.text
        assert "ratchet-chart" in resp.text
        assert "Chart" in resp.text

    def test_research_view_invalid_name(self, research_client: TestClient):
        resp = research_client.get("/research/../etc")
        assert resp.status_code in (400, 404, 422)
