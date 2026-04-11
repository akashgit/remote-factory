"""Tests for factory.models — all Pydantic v2 strict models."""

import pytest
from datetime import datetime

from factory.models import (
    CostBudget,
    CompositeScore,
    EvalDimension,
    EvalProfile,
    EvalResult,
    ExperimentRecord,
    FactoryConfig,
    Hypothesis,
    ProjectProfile,
    ProjectState,
)


class TestProjectState:
    def test_all_states(self):
        assert ProjectState.NO_REPO.value == "no_repo"
        assert ProjectState.REPO_INCOMPLETE.value == "incomplete"
        assert ProjectState.NO_FACTORY.value == "no_factory"
        assert ProjectState.EVALS_PENDING_REVIEW.value == "evals_pending_review"
        assert ProjectState.HAS_FACTORY.value == "has_factory"

    def test_state_count(self):
        assert len(ProjectState) == 5


class TestFactoryConfig:
    def test_valid_config(self, sample_config):
        assert sample_config.goal == "Build a test project"
        assert len(sample_config.scope) == 2
        assert sample_config.eval_threshold == 0.8

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            FactoryConfig(
                goal="x", scope=[], guards=[], eval_command="x",
                eval_threshold=0.8, constraints=[], extra_field="bad",
            )

    def test_roundtrip_json(self, sample_config):
        data = sample_config.model_dump()
        restored = FactoryConfig(**data)
        assert restored == sample_config


class TestEvalResult:
    def test_valid_result(self):
        r = EvalResult(name="tests", score=1.0, weight=0.5, passed=True, details="ok")
        assert r.score == 1.0

    def test_rejects_extra(self):
        with pytest.raises(Exception):
            EvalResult(name="x", score=0.0, weight=1.0, passed=False, details="", extra="bad")


class TestCompositeScore:
    def test_passing_score(self):
        s = CompositeScore(total=0.9, results=[], guard_violations=[], passed=True)
        assert s.passed

    def test_failing_with_violations(self):
        s = CompositeScore(total=0.9, results=[], guard_violations=["violation"], passed=False)
        assert not s.passed


class TestEvalDimension:
    def test_valid_dimension(self):
        d = EvalDimension(
            name="tests", command="pytest", weight=0.5,
            parser="exit_code", description="Run tests", source="discovered",
        )
        assert d.source == "discovered"

    def test_with_regex(self):
        d = EvalDimension(
            name="coverage", command="pytest --cov", weight=0.2,
            parser="regex", regex_pattern=r"(\d+)%",
            description="Coverage", source="researched",
        )
        assert d.regex_pattern == r"(\d+)%"

    def test_valid_sources(self):
        for source in ("explicit", "discovered", "researched", "fallback"):
            d = EvalDimension(
                name="x", command="x", weight=0.5,
                parser="exit_code", description="x", source=source,
            )
            assert d.source == source


class TestEvalProfile:
    def test_valid_profile(self):
        p = EvalProfile(
            project_type="bot",
            dimensions=[
                EvalDimension(
                    name="tests", command="pytest", weight=1.0,
                    parser="exit_code", description="tests", source="discovered",
                )
            ],
            tier="discovered",
            confidence=0.8,
        )
        assert p.human_reviewed is False

    def test_human_reviewed_flag(self):
        p = EvalProfile(
            project_type="cli_tool",
            dimensions=[],
            tier="fallback",
            confidence=0.2,
            human_reviewed=True,
        )
        assert p.human_reviewed is True


class TestProjectProfile:
    def test_minimal_profile(self):
        p = ProjectProfile(
            name="test", language="python", project_type="cli_tool",
            has_tests=True, has_linter=True, has_type_checker=False, has_ci=False,
        )
        assert p.framework is None
        assert p.test_command is None

    def test_full_profile(self):
        p = ProjectProfile(
            name="test", language="python", framework="fastapi",
            project_type="web_app",
            has_tests=True, has_linter=True, has_type_checker=True, has_ci=True,
            test_command="pytest", lint_command="ruff check .",
            type_check_command="mypy src/", package_manager="uv",
        )
        assert p.framework == "fastapi"


class TestHypothesis:
    def test_valid_hypothesis(self):
        h = Hypothesis(
            description="Add tests",
            rationale="Coverage is low",
            expected_impact="tests score +0.2",
            target_files=["tests/test_new.py"],
        )
        assert len(h.target_files) == 1


class TestExperimentRecord:
    def test_valid_record(self):
        r = ExperimentRecord(
            id=1, timestamp=datetime.now(),
            hypothesis="Test hypothesis",
            change_summary="Added tests",
            issue_number=42, pr_number=43,
            score_before=0.8, score_after=0.9, delta=0.1,
            verdict="keep", cost_usd=1.5, notes="",
        )
        assert r.verdict == "keep"

    def test_nullable_fields(self):
        r = ExperimentRecord(
            id=1, timestamp=datetime.now(),
            hypothesis="x", change_summary="",
            issue_number=None, pr_number=None,
            score_before=None, score_after=None, delta=None,
            verdict="error", cost_usd=None, notes="crashed",
        )
        assert r.issue_number is None

    def test_valid_verdicts(self):
        for v in ("keep", "revert", "error"):
            r = ExperimentRecord(
                id=1, timestamp=datetime.now(),
                hypothesis="x", change_summary="",
                issue_number=None, pr_number=None,
                score_before=None, score_after=None, delta=None,
                verdict=v, cost_usd=None, notes="",
            )
            assert r.verdict == v


class TestCostBudget:
    def test_defaults(self):
        b = CostBudget()
        assert b.per_experiment_max == 2.0
        assert b.per_session_max == 10.0
        assert b.per_month_max == 100.0
        assert b.current_session_spent == 0.0

    def test_custom_budget(self):
        b = CostBudget(per_experiment_max=5.0, per_session_max=50.0)
        assert b.per_experiment_max == 5.0
