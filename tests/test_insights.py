"""Tests for factory.insights — cross-project analysis module."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from factory.insights import (
    _extract_patterns,
    analyze,
    classify_hypothesis,
    discover_projects,
    format_insights,
    load_all_histories,
)
from factory.models import ExperimentRecord, HypothesisOutcome


# ── fixtures ──────────────────────────────────────────────────────


def _record(
    id: int = 1,
    hypothesis: str = "Test hypothesis",
    verdict: str = "keep",
    score_before: float | None = 0.8,
    score_after: float | None = 0.9,
    delta: float | None = 0.1,
) -> ExperimentRecord:
    return ExperimentRecord(
        id=id,
        timestamp=datetime(2026, 4, 13, 12, 0, 0),
        hypothesis=hypothesis,
        change_summary="summary",
        issue_number=None,
        pr_number=None,
        score_before=score_before,
        score_after=score_after,
        delta=delta,
        verdict=verdict,
        cost_usd=None,
        notes="",
    )


# ── TestDiscoverProjects ──────────────────────────────────────────


class TestDiscoverProjects:
    def test_finds_project_with_results_tsv(self, tmp_path: Path) -> None:
        proj = tmp_path / "my-project"
        proj.mkdir()
        factory = proj / ".factory"
        factory.mkdir()
        (factory / "results.tsv").write_text("id\tverdict\n1\tkeep\n")

        result = discover_projects(tmp_path)
        assert len(result) == 1
        assert result[0] == proj

    def test_ignores_dirs_without_factory(self, tmp_path: Path) -> None:
        (tmp_path / "not-factory").mkdir()
        result = discover_projects(tmp_path)
        assert result == []

    def test_ignores_files(self, tmp_path: Path) -> None:
        (tmp_path / "somefile.txt").write_text("hi")
        result = discover_projects(tmp_path)
        assert result == []

    def test_returns_empty_for_nonexistent_dir(self) -> None:
        result = discover_projects(Path("/nonexistent/path"))
        assert result == []

    def test_finds_multiple_projects_sorted(self, tmp_path: Path) -> None:
        for name in ["zebra", "alpha", "middle"]:
            proj = tmp_path / name
            proj.mkdir()
            factory = proj / ".factory"
            factory.mkdir()
            (factory / "results.tsv").write_text("id\tverdict\n")

        result = discover_projects(tmp_path)
        assert len(result) == 3
        names = [p.name for p in result]
        assert names == ["alpha", "middle", "zebra"]

    def test_skips_empty_factory_dir(self, tmp_path: Path) -> None:
        proj = tmp_path / "has-factory-dir"
        proj.mkdir()
        (proj / ".factory").mkdir()
        # No results.tsv inside
        result = discover_projects(tmp_path)
        assert result == []


# ── TestClassifyHypothesis ────────────────────────────────────────


class TestClassifyHypothesis:
    def test_bugfix(self) -> None:
        assert classify_hypothesis("Fix crash in login handler") == "bugfix"

    def test_observability(self) -> None:
        assert classify_hypothesis("Add structlog to data pipeline") == "observability"

    def test_coverage(self) -> None:
        assert classify_hypothesis("Increase test coverage to 90%") == "coverage"

    def test_testing(self) -> None:
        assert classify_hypothesis("Add pytest tests for auth module") == "testing"

    def test_lint(self) -> None:
        assert classify_hypothesis("Run ruff lint check on cli.py") == "lint"

    def test_type_safety(self) -> None:
        assert classify_hypothesis("Resolve mypy type check warnings") == "type_safety"

    def test_refactoring(self) -> None:
        assert classify_hypothesis("Refactor database layer for clarity") == "refactoring"

    def test_performance(self) -> None:
        assert classify_hypothesis("Optimize query performance with cache") == "performance"

    def test_eval_improvement(self) -> None:
        assert classify_hypothesis("Add new eval dimension for security") == "eval_improvement"

    def test_agent_improvement(self) -> None:
        assert classify_hypothesis("Improve agent dispatch system") == "agent_improvement"

    def test_prompt_engineering(self) -> None:
        assert classify_hypothesis("Rewrite instruction prompt for builder") == "prompt_engineering"

    def test_infrastructure(self) -> None:
        assert classify_hypothesis("Add tmux integration for factory") == "infrastructure"

    def test_feature(self) -> None:
        assert classify_hypothesis("Add new page for user dashboard") == "feature"

    def test_default_is_feature(self) -> None:
        assert classify_hypothesis("Improve the overall quality") == "feature"

    def test_case_insensitive(self) -> None:
        assert classify_hypothesis("FIX A BUG IN THE SYSTEM") == "bugfix"

    def test_priority_bugfix_over_testing(self) -> None:
        # "fix" is higher priority than "test"
        assert classify_hypothesis("Fix failing tests") == "bugfix"

    def test_priority_observability_over_feature(self) -> None:
        assert classify_hypothesis("Add logging to new endpoint") == "observability"


# ── TestLoadAllHistories ──────────────────────────────────────────


class TestLoadAllHistories:
    def test_loads_from_multiple_projects(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Create two projects with TSV data
        for name, count in [("proj-a", 3), ("proj-b", 2)]:
            proj = tmp_path / name
            proj.mkdir()
            factory = proj / ".factory"
            factory.mkdir()
            lines = ["id\ttimestamp\thypothesis\tchange_summary\tissue_number\tpr_number\t"
                      "score_before\tscore_after\tdelta\tverdict\tcost_usd\tnotes"]
            for i in range(1, count + 1):
                lines.append(
                    f"{i}\t2026-04-13T12:00:00\tHypothesis {i}\tsummary\t\t\t\t\t\tkeep\t\t"
                )
            (factory / "results.tsv").write_text("\n".join(lines) + "\n")

        paths = [tmp_path / "proj-a", tmp_path / "proj-b"]
        histories = load_all_histories(paths)
        assert "proj-a" in histories
        assert "proj-b" in histories
        assert len(histories["proj-a"]) == 3
        assert len(histories["proj-b"]) == 2

    def test_empty_tsv_excluded(self, tmp_path: Path) -> None:
        proj = tmp_path / "empty"
        proj.mkdir()
        factory = proj / ".factory"
        factory.mkdir()
        (factory / "results.tsv").write_text(
            "id\ttimestamp\thypothesis\tchange_summary\tissue_number\tpr_number\t"
            "score_before\tscore_after\tdelta\tverdict\tcost_usd\tnotes\n"
        )
        histories = load_all_histories([proj])
        assert histories == {}

    def test_missing_tsv_excluded(self, tmp_path: Path) -> None:
        proj = tmp_path / "no-tsv"
        proj.mkdir()
        (proj / ".factory").mkdir()
        histories = load_all_histories([proj])
        assert histories == {}


# ── TestAnalyze ───────────────────────────────────────────────────


class TestAnalyze:
    def test_basic_stats(self) -> None:
        histories = {
            "proj-a": [
                _record(id=1, hypothesis="Fix bug in auth", verdict="keep"),
                _record(id=2, hypothesis="Fix crash handler", verdict="keep"),
                _record(id=3, hypothesis="Fix error in parser", verdict="revert"),
            ],
        }
        insights = analyze(histories)
        assert len(insights.projects) == 1
        assert insights.projects[0].name == "proj-a"
        assert insights.projects[0].keep_count == 2
        assert insights.projects[0].revert_count == 1
        assert insights.projects[0].keep_rate == pytest.approx(2 / 3)

    def test_multi_project(self) -> None:
        histories = {
            "proj-a": [_record(id=1, verdict="keep")],
            "proj-b": [_record(id=1, verdict="revert")],
        }
        insights = analyze(histories)
        assert len(insights.projects) == 2

    def test_category_stats(self) -> None:
        histories = {
            "proj-a": [
                _record(id=1, hypothesis="Fix bug one", verdict="keep"),
                _record(id=2, hypothesis="Fix bug two", verdict="keep"),
                _record(id=3, hypothesis="Fix bug three", verdict="keep"),
                _record(id=4, hypothesis="Add new page", verdict="revert"),
                _record(id=5, hypothesis="Add new route", verdict="revert"),
                _record(id=6, hypothesis="Add new command", verdict="revert"),
            ],
        }
        insights = analyze(histories)
        assert "bugfix" in insights.category_stats
        assert insights.category_stats["bugfix"]["rate"] == pytest.approx(1.0)
        assert "feature" in insights.category_stats
        assert insights.category_stats["feature"]["rate"] == pytest.approx(0.0)

    def test_winning_categories(self) -> None:
        histories = {
            "proj-a": [
                _record(id=i, hypothesis="Fix bug", verdict="keep")
                for i in range(1, 5)
            ],
        }
        insights = analyze(histories)
        assert "bugfix" in insights.winning_categories

    def test_losing_categories(self) -> None:
        histories = {
            "proj-a": [
                _record(id=1, hypothesis="Add page one", verdict="revert"),
                _record(id=2, hypothesis="Add page two", verdict="error"),
                _record(id=3, hypothesis="Add page three", verdict="revert"),
            ],
        }
        insights = analyze(histories)
        assert "feature" in insights.losing_categories

    def test_needs_minimum_experiments(self) -> None:
        histories = {
            "proj-a": [
                _record(id=1, hypothesis="Fix a bug", verdict="keep"),
                _record(id=2, hypothesis="Fix another bug", verdict="keep"),
            ],
        }
        insights = analyze(histories)
        # Only 2 experiments, threshold is 3
        assert "bugfix" not in insights.winning_categories

    def test_latest_score(self) -> None:
        histories = {
            "proj-a": [
                _record(id=1, score_after=0.8),
                _record(id=2, score_after=0.95),
            ],
        }
        insights = analyze(histories)
        assert insights.projects[0].latest_score == pytest.approx(0.95)

    def test_no_scores(self) -> None:
        histories = {
            "proj-a": [_record(id=1, score_after=None)],
        }
        insights = analyze(histories)
        assert insights.projects[0].latest_score is None

    def test_error_count(self) -> None:
        histories = {
            "proj-a": [
                _record(id=1, verdict="error"),
                _record(id=2, verdict="error"),
                _record(id=3, verdict="keep"),
            ],
        }
        insights = analyze(histories)
        assert insights.projects[0].error_count == 2

    def test_empty_histories(self) -> None:
        insights = analyze({})
        assert insights.projects == []
        assert insights.outcomes == []
        assert insights.winning_categories == []
        assert insights.losing_categories == []


# ── TestExtractPatterns ───────────────────────────────────────────


class TestExtractPatterns:
    def test_reliable_pattern(self) -> None:
        outcomes = [
            HypothesisOutcome(
                hypothesis=f"Fix bug {i}", verdict="keep",
                category="bugfix", project=f"proj-{chr(97 + i % 2)}",
            )
            for i in range(5)
        ]
        stats = {"bugfix": {"total": 5, "kept": 5, "rate": 1.0}}
        patterns = _extract_patterns(outcomes, stats)
        reliable = [p for p in patterns if p.name == "bugfix_reliable"]
        assert len(reliable) == 1
        assert "100%" in reliable[0].description

    def test_risky_pattern(self) -> None:
        outcomes = [
            HypothesisOutcome(
                hypothesis=f"Add feature {i}", verdict="revert",
                category="feature", project=f"proj-{chr(97 + i % 2)}",
            )
            for i in range(4)
        ]
        stats = {"feature": {"total": 4, "kept": 0, "rate": 0.0}}
        patterns = _extract_patterns(outcomes, stats)
        risky = [p for p in patterns if p.name == "feature_risky"]
        assert len(risky) == 1

    def test_kept_with_regression(self) -> None:
        outcomes = [
            HypothesisOutcome(
                hypothesis="Risky change", verdict="keep",
                category="feature", project="proj-a", delta=-0.05,
            ),
        ]
        stats = {"feature": {"total": 1, "kept": 1, "rate": 1.0}}
        patterns = _extract_patterns(outcomes, stats)
        regression = [p for p in patterns if p.name == "kept_with_regression"]
        assert len(regression) == 1

    def test_no_pattern_for_small_sample(self) -> None:
        outcomes = [
            HypothesisOutcome(
                hypothesis="Fix bug", verdict="keep",
                category="bugfix", project="proj-a",
            ),
        ]
        stats = {"bugfix": {"total": 1, "kept": 1, "rate": 1.0}}
        patterns = _extract_patterns(outcomes, stats)
        reliable = [p for p in patterns if "reliable" in p.name]
        assert reliable == []

    def test_no_pattern_single_project(self) -> None:
        outcomes = [
            HypothesisOutcome(
                hypothesis=f"Fix bug {i}", verdict="keep",
                category="bugfix", project="proj-a",
            )
            for i in range(5)
        ]
        stats = {"bugfix": {"total": 5, "kept": 5, "rate": 1.0}}
        patterns = _extract_patterns(outcomes, stats)
        # Need >= 2 projects for reliable pattern
        reliable = [p for p in patterns if p.name == "bugfix_reliable"]
        assert reliable == []


# ── TestFormatInsights ────────────────────────────────────────────


class TestFormatInsights:
    def test_contains_header(self) -> None:
        histories = {
            "proj-a": [_record(id=1, hypothesis="Fix a bug", verdict="keep")],
        }
        insights = analyze(histories)
        report = format_insights(insights)
        assert "Cross-Project Insights" in report

    def test_contains_project_summary(self) -> None:
        histories = {
            "proj-a": [_record(id=1, verdict="keep")],
        }
        insights = analyze(histories)
        report = format_insights(insights)
        assert "proj-a" in report
        assert "100% keep rate" in report

    def test_contains_category_table(self) -> None:
        histories = {
            "proj-a": [_record(id=1, hypothesis="Fix a bug", verdict="keep")],
        }
        insights = analyze(histories)
        report = format_insights(insights)
        assert "Category Success Rates" in report
        assert "bugfix" in report

    def test_empty_insights(self) -> None:
        insights = analyze({})
        report = format_insights(insights)
        assert "0 projects" in report
        assert "0 experiments" in report

    def test_winning_section(self) -> None:
        histories = {
            "proj-a": [
                _record(id=i, hypothesis="Fix bug", verdict="keep")
                for i in range(1, 5)
            ],
        }
        insights = analyze(histories)
        report = format_insights(insights)
        assert "Winning Strategies" in report

    def test_patterns_section(self) -> None:
        histories = {
            "proj-a": [
                _record(id=1, hypothesis="Risky change", verdict="keep", delta=-0.05),
            ],
        }
        insights = analyze(histories)
        report = format_insights(insights)
        assert "Patterns" in report
        assert "kept_with_regression" in report


# ── TestCmdInsights ───────────────────────────────────────────────


class TestCmdInsights:
    def test_writes_insights_file(self, tmp_path: Path) -> None:
        from factory.cli import cmd_insights

        # Create a project with results.tsv
        proj = tmp_path / "my-project"
        proj.mkdir()
        factory = proj / ".factory"
        factory.mkdir()
        lines = [
            "id\ttimestamp\thypothesis\tchange_summary\tissue_number\tpr_number\t"
            "score_before\tscore_after\tdelta\tverdict\tcost_usd\tnotes",
            "1\t2026-04-13T12:00:00\tFix a bug\tsummary\t\t\t\t\t\tkeep\t\t",
        ]
        (factory / "results.tsv").write_text("\n".join(lines) + "\n")

        # Create a target project for output
        target = tmp_path / "target"
        target.mkdir()
        (target / ".factory").mkdir()

        args = type("Args", (), {
            "path": str(target),
            "projects_dir": str(tmp_path),
        })()
        ret = cmd_insights(args)
        assert ret == 0

        out_path = target / ".factory" / "strategy" / "insights.md"
        assert out_path.exists()
        content = out_path.read_text()
        assert "Cross-Project Insights" in content

    def test_no_projects_found(self, tmp_path: Path) -> None:
        from factory.cli import cmd_insights

        target = tmp_path / "target"
        target.mkdir()

        args = type("Args", (), {
            "path": str(target),
            "projects_dir": str(tmp_path),
        })()
        ret = cmd_insights(args)
        assert ret == 0
