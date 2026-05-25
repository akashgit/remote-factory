"""Tests for factory.models — all Pydantic v2 strict models."""

import pytest
from datetime import datetime

from factory.models import (
    AggregateMethod,
    CostBudget,
    CostBudgetConfig,
    CompositeScore,
    CycleState,
    EvalDimension,
    EvalProfile,
    EvalResult,
    ExperimentRecord,
    FactoryConfig,
    Hypothesis,
    InnerLoopConfig,
    OuterLoopConfig,
    ProjectProfile,
    ProjectState,
    ResearchTarget,
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


class TestResearchTarget:
    def test_valid_target(self):
        t = ResearchTarget(
            objective="Minimize latency",
            metric="p99_latency_ms",
            target=50.0,
            run_command="python benchmark.py",
            result_path="results/benchmark.json",
        )
        assert t.objective == "Minimize latency"
        assert t.result_parser == "json"
        assert t.timeout == 3600

    def test_custom_timeout(self):
        t = ResearchTarget(
            objective="Maximize accuracy",
            metric="accuracy",
            target=0.95,
            run_command="python train.py",
            result_path="results.json",
            timeout=7200,
        )
        assert t.result_parser == "json"
        assert t.timeout == 7200

    def test_rejects_invalid_parser(self):
        with pytest.raises(Exception):
            ResearchTarget(
                objective="x", metric="y", target=1.0,
                run_command="z", result_path="r",
                result_parser="exit_code",
            )

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            ResearchTarget(
                objective="x", metric="y", target=1.0,
                run_command="z", result_path="r", extra="bad",
            )


class TestCostBudgetConfig:
    def test_defaults_none(self):
        c = CostBudgetConfig()
        assert c.max_per_cycle is None
        assert c.max_total is None

    def test_custom_values(self):
        c = CostBudgetConfig(max_per_cycle=5.0, max_total=100.0)
        assert c.max_per_cycle == 5.0
        assert c.max_total == 100.0

    def test_partial_values(self):
        c = CostBudgetConfig(max_per_cycle=2.5)
        assert c.max_per_cycle == 2.5
        assert c.max_total is None

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            CostBudgetConfig(max_per_cycle=1.0, extra="bad")


class TestFactoryConfigResearchFields:
    def test_defaults_preserve_backward_compat(self):
        config = FactoryConfig(
            goal="Test", scope=[], guards=[], eval_command="pytest",
            eval_threshold=0.8, constraints=[],
        )
        assert config.research_target is None
        assert config.mutable_surfaces == []
        assert config.fixed_surfaces == []
        assert config.research_constraints == []
        assert config.cost_budget is None

    def test_with_research_target(self):
        rt = ResearchTarget(
            objective="Maximize F1",
            metric="f1_score",
            target=0.9,
            run_command="python eval.py",
            result_path="output.json",
        )
        config = FactoryConfig(
            goal="Research", scope=[], guards=[], eval_command="pytest",
            eval_threshold=0.8, constraints=[], research_target=rt,
            mutable_surfaces=["src/model.py"], fixed_surfaces=["data/"],
            research_constraints=["No extra dependencies"],
            cost_budget=CostBudgetConfig(max_per_cycle=3.0),
        )
        assert config.research_target is not None
        assert config.research_target.objective == "Maximize F1"
        assert config.mutable_surfaces == ["src/model.py"]
        assert config.fixed_surfaces == ["data/"]
        assert config.research_constraints == ["No extra dependencies"]
        assert config.cost_budget is not None
        assert config.cost_budget.max_per_cycle == 3.0

    def test_roundtrip_json_with_research(self):
        rt = ResearchTarget(
            objective="Minimize loss",
            metric="val_loss",
            target=0.01,
            run_command="python train.py",
            result_path="metrics.json",
        )
        config = FactoryConfig(
            goal="Research", scope=["src/"], guards=[], eval_command="pytest",
            eval_threshold=0.8, constraints=[], research_target=rt,
            mutable_surfaces=["src/"], fixed_surfaces=["data/"],
        )
        data = config.model_dump()
        restored = FactoryConfig(**data)
        assert restored.research_target is not None
        assert restored.research_target.objective == "Minimize loss"
        assert restored == config


class TestAggregateMethod:
    def test_all_values(self):
        assert AggregateMethod.mean.value == "mean"
        assert AggregateMethod.median.value == "median"
        assert AggregateMethod.max.value == "max"
        assert AggregateMethod.all_pass.value == "all_pass"

    def test_count(self):
        assert len(AggregateMethod) == 4

    def test_string_coercion(self):
        assert AggregateMethod("mean") == AggregateMethod.mean
        assert AggregateMethod("all_pass") == AggregateMethod.all_pass

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            AggregateMethod("invalid")


class TestInnerLoopConfig:
    def test_defaults(self):
        c = InnerLoopConfig()
        assert c.runs_per_cycle == 1
        assert c.aggregate == AggregateMethod.mean
        assert c.max_runs_per_cycle is None

    def test_custom_values(self):
        c = InnerLoopConfig(
            runs_per_cycle=5,
            aggregate=AggregateMethod.median,
            max_runs_per_cycle=10,
        )
        assert c.runs_per_cycle == 5
        assert c.aggregate == AggregateMethod.median
        assert c.max_runs_per_cycle == 10

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            InnerLoopConfig(runs_per_cycle=1, extra="bad")

    def test_strict_mode(self):
        """Strict mode rejects string where int is expected."""
        with pytest.raises(Exception):
            InnerLoopConfig(runs_per_cycle="not_an_int")  # type: ignore[arg-type]

    def test_roundtrip_json(self):
        c = InnerLoopConfig(runs_per_cycle=3, aggregate=AggregateMethod.max)
        data = c.model_dump()
        restored = InnerLoopConfig(**data)
        assert restored == c


class TestOuterLoopConfig:
    def test_defaults(self):
        c = OuterLoopConfig()
        assert c.plateau_threshold == 3
        assert c.max_escalation_cycles is None
        assert c.inner_surfaces == []
        assert c.outer_surfaces == []

    def test_custom_values(self):
        c = OuterLoopConfig(
            plateau_threshold=5,
            max_escalation_cycles=10,
            inner_surfaces=["src/*.py"],
            outer_surfaces=["config/*.yaml"],
        )
        assert c.plateau_threshold == 5
        assert c.max_escalation_cycles == 10
        assert c.inner_surfaces == ["src/*.py"]
        assert c.outer_surfaces == ["config/*.yaml"]

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            OuterLoopConfig(plateau_threshold=3, extra="bad")

    def test_strict_mode(self):
        with pytest.raises(Exception):
            OuterLoopConfig(plateau_threshold="not_an_int")  # type: ignore[arg-type]

    def test_roundtrip_json(self):
        c = OuterLoopConfig(
            plateau_threshold=4,
            inner_surfaces=["a.py", "b.py"],
            outer_surfaces=["c.py"],
        )
        data = c.model_dump()
        restored = OuterLoopConfig(**data)
        assert restored == c


class TestFactoryConfigInnerOuterLoop:
    def test_defaults_none(self):
        config = FactoryConfig(
            goal="Test", scope=[], guards=[], eval_command="pytest",
            eval_threshold=0.8, constraints=[],
        )
        assert config.inner_loop is None
        assert config.outer_loop is None

    def test_with_inner_loop(self):
        il = InnerLoopConfig(runs_per_cycle=3, aggregate=AggregateMethod.median)
        config = FactoryConfig(
            goal="Test", scope=[], guards=[], eval_command="pytest",
            eval_threshold=0.8, constraints=[], inner_loop=il,
        )
        assert config.inner_loop is not None
        assert config.inner_loop.runs_per_cycle == 3
        assert config.inner_loop.aggregate == AggregateMethod.median

    def test_with_outer_loop(self):
        ol = OuterLoopConfig(
            plateau_threshold=5,
            inner_surfaces=["src/"],
            outer_surfaces=["config/"],
        )
        config = FactoryConfig(
            goal="Test", scope=[], guards=[], eval_command="pytest",
            eval_threshold=0.8, constraints=[], outer_loop=ol,
        )
        assert config.outer_loop is not None
        assert config.outer_loop.plateau_threshold == 5

    def test_roundtrip_json_with_loops(self):
        il = InnerLoopConfig(runs_per_cycle=5, aggregate=AggregateMethod.all_pass)
        ol = OuterLoopConfig(
            plateau_threshold=4,
            max_escalation_cycles=8,
            inner_surfaces=["src/model.py"],
            outer_surfaces=["config/"],
        )
        config = FactoryConfig(
            goal="Research", scope=["src/"], guards=[], eval_command="pytest",
            eval_threshold=0.8, constraints=[], inner_loop=il, outer_loop=ol,
        )
        data = config.model_dump()
        restored = FactoryConfig(**data)
        assert restored == config
        assert restored.inner_loop is not None
        assert restored.inner_loop.aggregate == AggregateMethod.all_pass
        assert restored.outer_loop is not None
        assert restored.outer_loop.max_escalation_cycles == 8


class TestCycleStateResearchMode:
    def test_research_mode_accepted(self):
        cs = CycleState(
            cycle_id="test-123",
            started_at=datetime.now(),
            mode="research",
        )
        assert cs.mode == "research"
