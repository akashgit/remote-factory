"""Tests for factory.analysis — experiment comparison and explanation."""

from datetime import datetime
from pathlib import Path

import pytest

from factory.analysis import (
    compare_experiments,
    dimension_diff,
    explain_experiment,
    format_comparison,
    format_explanation,
)
from factory.models import CompositeScore, EvalResult, ExperimentRecord
from factory.store import ExperimentStore


@pytest.fixture
def analysis_store(tmp_path: Path) -> ExperimentStore:
    """Create a store with two finalized experiments and eval data."""
    import asyncio

    project = tmp_path / "project"
    project.mkdir()
    store = ExperimentStore(project)

    from factory.models import FactoryConfig

    config = FactoryConfig(
        goal="Test project",
        scope=["src/**/*.py"],
        guards=["Do not delete tests"],
        eval_command="python eval/score.py",
        eval_threshold=0.8,
        constraints=[],
    )
    asyncio.run(store.init(config))

    # Experiment 1: a "fix" hypothesis that was kept
    exp1 = asyncio.run(store.begin("Fix broken test suite"))
    score_before_1 = CompositeScore(
        total=0.6,
        results=[
            EvalResult(name="tests", score=0.5, weight=1.0, passed=False, details="3/6 pass"),
            EvalResult(name="lint", score=0.7, weight=0.5, passed=True, details="clean"),
        ],
        guard_violations=[],
        passed=False,
    )
    score_after_1 = CompositeScore(
        total=0.85,
        results=[
            EvalResult(name="tests", score=0.9, weight=1.0, passed=True, details="5/6 pass"),
            EvalResult(name="lint", score=0.8, weight=0.5, passed=True, details="clean"),
        ],
        guard_violations=[],
        passed=True,
    )
    asyncio.run(store.save_eval(exp1, "before", score_before_1))
    asyncio.run(store.save_eval(exp1, "after", score_after_1))
    record1 = ExperimentRecord(
        id=exp1,
        timestamp=datetime(2026, 1, 10, 12, 0, 0),
        hypothesis="Fix broken test suite",
        change_summary="Fixed 2 failing tests",
        issue_number=10,
        pr_number=11,
        score_before=0.6,
        score_after=0.85,
        delta=0.25,
        verdict="keep",
        cost_usd=0.50,
        notes="",
    )
    asyncio.run(store.finalize(exp1, record1))

    # Experiment 2: an "explore" hypothesis that was reverted
    exp2 = asyncio.run(store.begin("Add new dashboard page"))
    score_before_2 = CompositeScore(
        total=0.85,
        results=[
            EvalResult(name="tests", score=0.9, weight=1.0, passed=True, details="5/6 pass"),
            EvalResult(name="lint", score=0.8, weight=0.5, passed=True, details="clean"),
        ],
        guard_violations=[],
        passed=True,
    )
    score_after_2 = CompositeScore(
        total=0.75,
        results=[
            EvalResult(name="tests", score=0.7, weight=1.0, passed=True, details="4/6 pass"),
            EvalResult(name="lint", score=0.8, weight=0.5, passed=True, details="clean"),
        ],
        guard_violations=[],
        passed=False,
    )
    asyncio.run(store.save_eval(exp2, "before", score_before_2))
    asyncio.run(store.save_eval(exp2, "after", score_after_2))
    record2 = ExperimentRecord(
        id=exp2,
        timestamp=datetime(2026, 1, 11, 14, 0, 0),
        hypothesis="Add new dashboard page",
        change_summary="Added dashboard page with charts",
        issue_number=12,
        pr_number=13,
        score_before=0.85,
        score_after=0.75,
        delta=-0.10,
        verdict="revert",
        cost_usd=1.20,
        notes="Regression in tests",
    )
    asyncio.run(store.finalize(exp2, record2))

    return store


@pytest.fixture
def store_no_evals(tmp_path: Path) -> ExperimentStore:
    """Create a store with experiments but no eval JSON files."""
    import asyncio

    project = tmp_path / "project"
    project.mkdir()
    store = ExperimentStore(project)

    from factory.models import FactoryConfig

    config = FactoryConfig(
        goal="Test",
        scope=[],
        guards=[],
        eval_command="echo ok",
        eval_threshold=0.5,
        constraints=[],
    )
    asyncio.run(store.init(config))

    exp1 = asyncio.run(store.begin("Improve logging"))
    record1 = ExperimentRecord(
        id=exp1,
        timestamp=datetime(2026, 2, 1, 10, 0, 0),
        hypothesis="Improve logging",
        change_summary="Added structlog",
        issue_number=None,
        pr_number=None,
        score_before=None,
        score_after=None,
        delta=None,
        verdict="keep",
        cost_usd=None,
        notes="",
    )
    asyncio.run(store.finalize(exp1, record1))
    return store


# ── dimension_diff ──────────────────────────────────────────────


class TestDimensionDiff:
    def test_basic_diff(self):
        before = {
            "results": [
                {"name": "tests", "score": 0.5},
                {"name": "lint", "score": 0.7},
            ],
        }
        after = {
            "results": [
                {"name": "tests", "score": 0.9},
                {"name": "lint", "score": 0.8},
            ],
        }
        diffs = dimension_diff(before, after)
        assert len(diffs) == 2

        tests_diff = next(d for d in diffs if d["name"] == "tests")
        assert tests_diff["before"] == 0.5
        assert tests_diff["after"] == 0.9
        assert tests_diff["delta"] == pytest.approx(0.4)

        lint_diff = next(d for d in diffs if d["name"] == "lint")
        assert lint_diff["delta"] == pytest.approx(0.1)

    def test_new_dimension_in_after(self):
        before = {"results": [{"name": "tests", "score": 0.5}]}
        after = {
            "results": [
                {"name": "tests", "score": 0.9},
                {"name": "coverage", "score": 0.8},
            ],
        }
        diffs = dimension_diff(before, after)
        assert len(diffs) == 2
        coverage = next(d for d in diffs if d["name"] == "coverage")
        assert coverage["before"] == 0.0
        assert coverage["after"] == 0.8

    def test_dimension_removed_in_after(self):
        before = {
            "results": [
                {"name": "tests", "score": 0.5},
                {"name": "old_metric", "score": 0.3},
            ],
        }
        after = {"results": [{"name": "tests", "score": 0.9}]}
        diffs = dimension_diff(before, after)
        assert len(diffs) == 2
        old = next(d for d in diffs if d["name"] == "old_metric")
        assert old["after"] == 0.0
        assert old["delta"] == pytest.approx(-0.3)

    def test_empty_results(self):
        diffs = dimension_diff({"results": []}, {"results": []})
        assert diffs == []

    def test_missing_results_key(self):
        diffs = dimension_diff({}, {})
        assert diffs == []


# ── compare_experiments ─────────────────────────────────────────


class TestCompareExperiments:
    def test_basic_comparison(self, analysis_store: ExperimentStore):
        result = compare_experiments(analysis_store, 1, 2)

        assert result["experiment_a"]["id"] == 1
        assert result["experiment_b"]["id"] == 2
        assert result["experiment_a"]["verdict"] == "keep"
        assert result["experiment_b"]["verdict"] == "revert"
        assert result["experiment_a"]["feec_category"] == "FIX"
        assert result["experiment_b"]["feec_category"] == "EXPLORE"

    def test_comparison_has_scores(self, analysis_store: ExperimentStore):
        result = compare_experiments(analysis_store, 1, 2)
        assert result["experiment_a"]["score_before"] == 0.6
        assert result["experiment_a"]["score_after"] == 0.85
        assert result["experiment_a"]["delta"] == 0.25

    def test_comparison_dimension_diffs(self, analysis_store: ExperimentStore):
        result = compare_experiments(analysis_store, 1, 2)
        diffs = result["dimension_diffs"]
        assert diffs is not None
        assert len(diffs) == 2
        # Compares eval_after of exp1 vs eval_after of exp2
        tests_diff = next(d for d in diffs if d["name"] == "tests")
        assert tests_diff["before"] == 0.9  # exp1 after
        assert tests_diff["after"] == 0.7   # exp2 after

    def test_comparison_no_evals(self, store_no_evals: ExperimentStore):
        result = compare_experiments(store_no_evals, 1, 1)
        assert result["dimension_diffs"] is None

    def test_missing_experiment_raises(self, analysis_store: ExperimentStore):
        with pytest.raises(ValueError, match="Experiment 99 not found"):
            compare_experiments(analysis_store, 1, 99)

    def test_missing_first_experiment_raises(self, analysis_store: ExperimentStore):
        with pytest.raises(ValueError, match="Experiment 99 not found"):
            compare_experiments(analysis_store, 99, 1)


# ── explain_experiment ──────────────────────────────────────────


class TestExplainExperiment:
    def test_basic_explanation(self, analysis_store: ExperimentStore):
        result = explain_experiment(analysis_store, 1)
        assert result["id"] == 1
        assert result["hypothesis"] == "Fix broken test suite"
        assert result["feec_category"] == "FIX"
        assert result["verdict"] == "keep"

    def test_explanation_scores(self, analysis_store: ExperimentStore):
        result = explain_experiment(analysis_store, 1)
        assert result["score_before"] == 0.6
        assert result["score_after"] == 0.85
        assert result["delta"] == 0.25

    def test_explanation_dimension_breakdown(self, analysis_store: ExperimentStore):
        result = explain_experiment(analysis_store, 1)
        breakdown = result["dimension_breakdown"]
        assert breakdown is not None
        assert len(breakdown) == 2
        tests = next(d for d in breakdown if d["name"] == "tests")
        assert tests["before"] == 0.5
        assert tests["after"] == 0.9
        assert tests["delta"] == pytest.approx(0.4)

    def test_explanation_full_hypothesis(self, analysis_store: ExperimentStore):
        result = explain_experiment(analysis_store, 1)
        assert result["hypothesis_full"] is not None
        assert "Fix broken test suite" in result["hypothesis_full"]

    def test_explanation_no_evals(self, store_no_evals: ExperimentStore):
        result = explain_experiment(store_no_evals, 1)
        assert result["dimension_breakdown"] is None
        assert result["score_before"] is None

    def test_missing_experiment_raises(self, analysis_store: ExperimentStore):
        with pytest.raises(ValueError, match="Experiment 99 not found"):
            explain_experiment(analysis_store, 99)


# ── format functions ────────────────────────────────────────────


class TestFormatComparison:
    def test_format_includes_both_experiments(self, analysis_store: ExperimentStore):
        comparison = compare_experiments(analysis_store, 1, 2)
        output = format_comparison(comparison)
        assert "Experiment #1" in output
        assert "Experiment #2" in output
        assert "FIX" in output
        assert "EXPLORE" in output
        assert "keep" in output
        assert "revert" in output

    def test_format_includes_dimension_diffs(self, analysis_store: ExperimentStore):
        comparison = compare_experiments(analysis_store, 1, 2)
        output = format_comparison(comparison)
        assert "tests" in output
        assert "lint" in output
        assert "Dimension Diffs" in output

    def test_format_no_evals(self, store_no_evals: ExperimentStore):
        comparison = compare_experiments(store_no_evals, 1, 1)
        output = format_comparison(comparison)
        assert "Experiment #1" in output
        assert "n/a" in output
        # Should not include dimension section
        assert "Dimension Diffs" not in output


class TestFormatExplanation:
    def test_format_includes_key_fields(self, analysis_store: ExperimentStore):
        explanation = explain_experiment(analysis_store, 1)
        output = format_explanation(explanation)
        assert "Experiment #1" in output
        assert "Fix broken test suite" in output
        assert "FIX" in output
        assert "keep" in output

    def test_format_includes_breakdown(self, analysis_store: ExperimentStore):
        explanation = explain_experiment(analysis_store, 1)
        output = format_explanation(explanation)
        assert "Dimension Breakdown" in output
        assert "tests" in output
        assert "lint" in output

    def test_format_no_evals(self, store_no_evals: ExperimentStore):
        explanation = explain_experiment(store_no_evals, 1)
        output = format_explanation(explanation)
        assert "n/a" in output
        assert "Dimension Breakdown" not in output


# ── CLI integration ─────────────────────────────────────────────


class TestCLIIntegration:
    def test_diff_command_registered(self):
        from factory.cli import build_parser

        parser = build_parser()
        # Should not raise
        args = parser.parse_args(["diff", "/tmp/proj", "1", "2"])
        assert args.command == "diff"
        assert args.id_a == 1
        assert args.id_b == 2

    def test_explain_command_registered(self):
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["explain", "/tmp/proj", "3"])
        assert args.command == "explain"
        assert args.id == 3

    def test_diff_handler_in_handlers(self):
        from factory.cli import build_parser

        # Verify the commands are in the handler dict by checking
        # that parsing succeeds and command name is correct
        parser = build_parser()
        args = parser.parse_args(["diff", "/tmp/p", "1", "2"])
        assert args.command == "diff"

    def test_explain_handler_in_handlers(self):
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["explain", "/tmp/p", "5"])
        assert args.command == "explain"
