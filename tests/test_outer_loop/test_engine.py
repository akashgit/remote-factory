"""Tests for SwarmEngine and BudgetTracker."""

from __future__ import annotations

import pytest

from factory.outer_loop.engine import BudgetTracker, SwarmEngine
from factory.outer_loop.evaluator import SwarmEvaluator
from factory.outer_loop.models import EvalResult, SwarmConfig
from factory.outer_loop.mutations import WeightedRandomStrategy
from factory.outer_loop.similarity import NoveltyFilter
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)


def _make_config(**overrides: object) -> SwarmConfig:
    defaults: dict[str, object] = {
        "benchmark": "test",
        "budget": 30,
        "population_size": 4,
        "tournament_size": 2,
        "mutation_rate": 0.3,
        "training_instances": ["t1", "t2"],
        "holdout_instances": ["h1"],
    }
    defaults.update(overrides)
    return SwarmConfig(**defaults)  # type: ignore[arg-type]


def _make_workflow() -> Workflow:
    return Workflow(
        name="test_evo",
        nodes={
            "study": FnNode(
                id="study", command="factory study", writes={".factory/obs.md"},
            ),
            "researcher": AgentNode(
                id="researcher", role=AgentRole.RESEARCHER,
                reads={".factory/obs.md"}, writes={".factory/research.md"},
            ),
            "strategist": AgentNode(
                id="strategist", role=AgentRole.STRATEGIST,
                reads={".factory/research.md"}, writes={".factory/current.md"},
            ),
            "builder": AgentNode(
                id="builder", role=AgentRole.BUILDER,
                reads={".factory/current.md"}, writes={".factory/build.md"},
            ),
            "gate": GateNode(
                id="gate", evaluator_type="fn",
                reads={".factory/build.md"},
            ),
        },
        edges=[
            Edge(source="study", target="researcher"),
            Edge(source="researcher", target="strategist"),
            Edge(source="strategist", target="builder"),
            Edge(source="builder", target="gate"),
            Edge(source="gate", target="builder", condition=VerdictType.RELOOP),
        ],
        start_node="study",
    )


def _make_deterministic_evaluator(
    base_score: float = 0.5, increment: float = 0.02,
) -> SwarmEvaluator:
    """Returns an evaluator that gives incrementally higher scores to different workflows."""
    counter: dict[str, int] = {"n": 0}

    def eval_fn(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
        counter["n"] += 1
        score = min(base_score + counter["n"] * increment, 1.0)
        return EvalResult(
            score=0.0, benchmark_score=score, hygiene_score=0.7,
            cost_usd=0.1, complexity=len(wf.nodes),
        )

    config = _make_config()
    return SwarmEvaluator(config, evaluator_fn=eval_fn)


class TestBudgetTracker:
    def test_initial_state(self) -> None:
        bt = BudgetTracker(100)
        assert bt.remaining == 100
        assert bt.consumed == 0
        assert not bt.exhausted
        assert bt.total_cost_usd == 0.0

    def test_consume(self) -> None:
        bt = BudgetTracker(10)
        bt.consume(3, cost_usd=1.5)
        assert bt.consumed == 3
        assert bt.remaining == 7
        assert bt.total_cost_usd == 1.5

    def test_exhausted(self) -> None:
        bt = BudgetTracker(5)
        bt.consume(5)
        assert bt.exhausted
        assert bt.remaining == 0

    def test_over_consume(self) -> None:
        bt = BudgetTracker(3)
        bt.consume(5)
        assert bt.exhausted
        assert bt.remaining == 0

    def test_elapsed(self) -> None:
        bt = BudgetTracker(10)
        assert bt.elapsed_seconds >= 0


class TestSwarmEngineSeed:
    def test_seed_creates_population(self) -> None:
        config = _make_config(population_size=4)
        evaluator = _make_deterministic_evaluator()
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()

        pop = engine.seed(wf)
        assert pop.size >= 1
        assert pop.size <= 4

    def test_seed_slot_zero_is_original(self) -> None:
        config = _make_config(population_size=3, designer_count=0)
        evaluator = _make_deterministic_evaluator()
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()

        pop = engine.seed(wf)
        individuals = pop.individuals
        original = [i for i in individuals if i.parent_id is None]
        assert len(original) == 1

    def test_seed_diversity(self) -> None:
        config = _make_config(population_size=4)
        evaluator = _make_deterministic_evaluator()
        novelty = NoveltyFilter(min_edit_distance=1)
        engine = SwarmEngine(config, evaluator, novelty_filter=novelty)
        wf = _make_workflow()

        pop = engine.seed(wf)
        ids = {i.id for i in pop.individuals}
        assert len(ids) == pop.size


class TestSwarmEngineEvolve:
    def test_evolve_generation_returns_summary(self) -> None:
        config = _make_config(budget=50, population_size=3)
        evaluator = _make_deterministic_evaluator()
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()
        pop = engine.seed(wf)

        summary = engine.evolve_generation(pop, generation=1)

        assert summary.generation == 1
        assert summary.population_size > 0
        assert summary.best_score >= 0
        assert summary.hyperparameters is not None
        assert summary.hyperparameters.generation == 1

    def test_evolve_updates_archive(self) -> None:
        config = _make_config(budget=50, population_size=3)
        evaluator = _make_deterministic_evaluator()
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()
        pop = engine.seed(wf)

        engine.evolve_generation(pop, generation=1)
        assert engine.archive.size > 0

    def test_hyperparameter_record_logged(self) -> None:
        config = _make_config(budget=50, population_size=3)
        evaluator = _make_deterministic_evaluator()
        strategy = WeightedRandomStrategy(mutation_rate=0.4, designer_ratio=0.2)
        engine = SwarmEngine(config, evaluator, strategy=strategy)
        wf = _make_workflow()
        pop = engine.seed(wf)

        summary = engine.evolve_generation(pop, generation=0)

        assert summary.hyperparameters is not None
        hp = summary.hyperparameters
        assert hp.mutation_rate == 0.4
        assert hp.designer_ratio == 0.2
        assert hp.population_size > 0


class TestSwarmEngineRun:
    def test_run_terminates_on_budget(self) -> None:
        config = _make_config(budget=30, population_size=2)
        evaluator = _make_deterministic_evaluator()
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()

        result = engine.run(wf)

        assert result.convergence_reason in ("budget_exhausted", "plateau", "early_stop_unchanged")
        assert result.total_evaluations > 0
        assert result.generations_completed >= 1
        assert len(result.trajectory) > 0

    def test_run_terminates_on_target_score(self) -> None:
        config = _make_config(budget=100, population_size=2, target_score=0.6)

        def high_score_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(
                score=0.0, benchmark_score=0.9, hygiene_score=0.9,
                cost_usd=0.01, complexity=3.0,
            )

        evaluator = SwarmEvaluator(config, evaluator_fn=high_score_eval)
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()

        result = engine.run(wf)
        assert result.convergence_reason == "target_score_reached"
        assert result.best_score >= 0.6

    def test_run_holdout_audit(self) -> None:
        config = _make_config(budget=15, population_size=2)

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            if "h1" in instances:
                return EvalResult(score=0.0, benchmark_score=0.6, hygiene_score=0.6)
            return EvalResult(score=0.0, benchmark_score=0.7, hygiene_score=0.7)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()

        result = engine.run(wf)
        assert result.holdout_score > 0
        assert isinstance(result.overfit_flag, bool)

    def test_run_hyperparameter_history(self) -> None:
        config = _make_config(budget=15, population_size=2)
        evaluator = _make_deterministic_evaluator()
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()

        result = engine.run(wf)
        assert len(result.hyperparameter_history) == result.generations_completed

    def test_run_pareto_front(self) -> None:
        config = _make_config(budget=15, population_size=2)
        evaluator = _make_deterministic_evaluator()
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()

        result = engine.run(wf)
        assert result.archive_size > 0
        assert len(result.pareto_front) > 0

    def test_run_result_fields(self) -> None:
        config = _make_config(budget=10, population_size=2)
        evaluator = _make_deterministic_evaluator()
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()

        result = engine.run(wf)
        assert result.best_workflow_data != {}
        assert result.total_cost_usd >= 0
        assert result.convergence_reason != ""


class TestSwarmEnginePlateau:
    def test_plateau_detection(self) -> None:
        config = _make_config(budget=100, population_size=2)

        def flat_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(
                score=0.0, benchmark_score=0.5, hygiene_score=0.5,
                cost_usd=0.01, complexity=3.0,
            )

        evaluator = SwarmEvaluator(config, evaluator_fn=flat_eval)
        strategy = WeightedRandomStrategy(mutation_rate=0.3)
        engine = SwarmEngine(config, evaluator, strategy=strategy)
        wf = _make_workflow()

        result = engine.run(wf)
        # With flat scores, should converge via plateau, early stop, or budget
        assert result.convergence_reason in ("plateau", "budget_exhausted", "early_stop_unchanged")

    def test_plateau_increases_mutation_rate(self) -> None:
        strategy = WeightedRandomStrategy(mutation_rate=0.3)
        assert strategy.get_mutation_rate(0) == 0.3
        strategy.on_plateau()
        assert strategy.get_mutation_rate(0) == pytest.approx(0.5)

    def test_improvement_resets_mutation_rate(self) -> None:
        strategy = WeightedRandomStrategy(mutation_rate=0.3)
        strategy.on_plateau()
        assert strategy.get_mutation_rate(0) == pytest.approx(0.5)
        strategy.on_improvement()
        assert strategy.get_mutation_rate(0) == 0.3


class TestSwarmEngineIntegration:
    def test_3_generations_with_mock(self) -> None:
        """Integration test: 3 generations, pop=4, mock fitness, verify trajectory."""
        config = _make_config(budget=50, population_size=4, target_score=None)

        eval_counter: dict[str, int] = {"n": 0}

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            eval_counter["n"] += 1
            score = min(0.3 + eval_counter["n"] * 0.01, 1.0)
            return EvalResult(
                score=0.0, benchmark_score=score, hygiene_score=0.6,
                cost_usd=0.05, complexity=float(len(wf.nodes)),
            )

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()

        result = engine.run(wf)

        assert result.generations_completed >= 1
        assert result.total_evaluations > 0
        assert len(result.trajectory) >= 1
        assert result.best_score > 0
        assert len(result.hyperparameter_history) == result.generations_completed

        for hp in result.hyperparameter_history:
            assert hp.mutation_rate > 0
            assert hp.population_size > 0
