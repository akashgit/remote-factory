"""Tests for factory.obsidian — note creation with Obsidian frontmatter."""

from datetime import datetime

import pytest

from factory.models import CompositeScore, EvalResult, ExperimentRecord
from factory.obsidian.notes import (
    write_experiment_note,
    write_project_dashboard,
    write_strategy_note,
)


@pytest.fixture(autouse=True)
def set_vault_path(obsidian_vault, monkeypatch):
    """Set OBSIDIAN_VAULT_PATH to temp dir for all tests."""
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(obsidian_vault))


@pytest.fixture
def sample_record() -> ExperimentRecord:
    return ExperimentRecord(
        id=1, timestamp=datetime(2026, 4, 11, 12, 0),
        hypothesis="Add session timeout handling",
        change_summary="Added timeout check in gateway.py",
        issue_number=11, pr_number=12,
        score_before=0.82, score_after=0.87, delta=0.05,
        verdict="keep", cost_usd=1.5, notes="",
    )


class TestExperimentNote:
    def test_creates_note(self, sample_record, obsidian_vault):
        path = write_experiment_note("cloud-gateway", sample_record)
        assert path.exists()
        assert "cloud-gateway-001.md" in path.name

    def test_note_has_frontmatter(self, sample_record, obsidian_vault):
        path = write_experiment_note("cloud-gateway", sample_record)
        content = path.read_text()
        assert content.startswith("---\n")
        assert "tags:" in content
        assert "  - factory" in content
        assert "  - experiment" in content
        assert "  - cloud-gateway" in content
        assert "verdict: keep" in content
        assert "experiment_id: 1" in content

    def test_note_has_hypothesis(self, sample_record, obsidian_vault):
        path = write_experiment_note("cloud-gateway", sample_record)
        content = path.read_text()
        assert "Add session timeout handling" in content

    def test_note_with_eval_details(self, sample_record, obsidian_vault):
        before = CompositeScore(
            total=0.82, passed=True, guard_violations=[],
            results=[EvalResult(name="tests", score=1.0, weight=0.5, passed=True, details="ok")],
        )
        after = CompositeScore(
            total=0.87, passed=True, guard_violations=[],
            results=[EvalResult(name="tests", score=1.0, weight=0.5, passed=True, details="ok")],
        )
        path = write_experiment_note("cloud-gateway", sample_record, before, after)
        content = path.read_text()
        assert "Eval Details" in content
        assert "| tests" in content

    def test_creates_parent_dirs(self, sample_record, obsidian_vault):
        path = write_experiment_note("new-project", sample_record)
        assert path.parent.exists()


class TestProjectDashboard:
    def test_creates_dashboard(self, obsidian_vault):
        path = write_project_dashboard("cloud-gateway", "has_factory", 0.87, [])
        assert path.exists()
        assert "cloud-gateway.md" in path.name

    def test_dashboard_has_frontmatter(self, obsidian_vault):
        path = write_project_dashboard("cloud-gateway", "has_factory", 0.87, [])
        content = path.read_text()
        assert "  - factory" in content
        assert "  - project" in content

    def test_dashboard_with_records(self, sample_record, obsidian_vault):
        path = write_project_dashboard("cloud-gateway", "has_factory", 0.87, [sample_record])
        content = path.read_text()
        assert "Experiments Run**: 1" in content
        assert "Kept**: 1" in content
        assert "[[cloud-gateway-001]]" in content

    def test_dashboard_with_eval_dimensions(self, obsidian_vault):
        dims = [{"name": "tests", "weight": 0.5, "description": "Run tests"}]
        path = write_project_dashboard("cloud-gateway", "has_factory", 0.87, [], dims)
        content = path.read_text()
        assert "Eval Dimensions" in content
        assert "tests" in content


class TestStrategyNote:
    def test_creates_strategy_note(self, obsidian_vault):
        path = write_strategy_note("cloud-gateway", "## Strategy\nFocus on reliability.")
        assert path.exists()
        assert "cloud-gateway-" in path.name

    def test_strategy_has_frontmatter(self, obsidian_vault):
        path = write_strategy_note("cloud-gateway", "content")
        content = path.read_text()
        assert "  - strategy" in content
        assert "  - cloud-gateway" in content
