"""Tests for SwarmEvaluator and FitnessCache."""

from __future__ import annotations

from factory.outer_loop.evaluator import FitnessCache, SwarmEvaluator
from factory.outer_loop.models import EvalResult, SwarmConfig
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
        "budget": 50,
        "training_instances": ["t1", "t2"],
        "holdout_instances": ["h1"],
    }
    defaults.update(overrides)
    return SwarmConfig(**defaults)  # type: ignore[arg-type]


def _make_simple_workflow(name: str = "test_wf") -> Workflow:
    return Workflow(
        name=name,
        nodes={
            "study": FnNode(id="study", command="echo study", writes={".factory/obs.md"}),
            "builder": AgentNode(
                id="builder", role=AgentRole.BUILDER, reads={".factory/obs.md"},
            ),
            "gate": GateNode(id="gate", evaluator_type="fn"),
        },
        edges=[
            Edge(source="study", target="builder"),
            Edge(source="builder", target="gate"),
        ],
        start_node="study",
    )


class TestFitnessCache:
    def test_miss_then_hit(self) -> None:
        cache = FitnessCache()
        wf = _make_simple_workflow()
        instances = ["t1", "t2"]

        assert cache.get(wf, instances) is None
        cache.put(wf, instances, 0.85, 1.5)
        result = cache.get(wf, instances)
        assert result is not None
        score, cost, ts = result
        assert score == 0.85
        assert cost == 1.5
        assert ts > 0

    def test_different_instances_separate_keys(self) -> None:
        cache = FitnessCache()
        wf = _make_simple_workflow()
        cache.put(wf, ["t1"], 0.7, 1.0)
        cache.put(wf, ["t1", "t2"], 0.85, 2.0)

        r1 = cache.get(wf, ["t1"])
        r2 = cache.get(wf, ["t1", "t2"])
        assert r1 is not None and r2 is not None
        assert r1[0] == 0.7
        assert r2[0] == 0.85

    def test_size(self) -> None:
        cache = FitnessCache()
        wf = _make_simple_workflow()
        assert cache.size == 0
        cache.put(wf, ["t1"], 0.5, 0.0)
        assert cache.size == 1


class TestSwarmEvaluator:
    def test_evaluate_with_fn(self) -> None:
        config = _make_config()

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(
                score=0.0, benchmark_score=0.8, hygiene_score=0.9,
                cost_usd=1.0, complexity=5.0,
            )

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        wf = _make_simple_workflow()
        result = evaluator.evaluate(wf, "/tmp/test", ["t1"])

        assert result.score > 0
        assert result.benchmark_score == 0.8

    def test_evaluate_uses_cache(self) -> None:
        config = _make_config()
        call_count = 0

        def counting_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            nonlocal call_count
            call_count += 1
            return EvalResult(score=0.0, benchmark_score=0.7, hygiene_score=0.8)

        evaluator = SwarmEvaluator(config, evaluator_fn=counting_eval)
        wf = _make_simple_workflow()
        evaluator.evaluate(wf, "/tmp/test", ["t1"])
        evaluator.evaluate(wf, "/tmp/test", ["t1"])
        assert call_count == 1

    def test_mandatory_component_rejection(self) -> None:
        config = _make_config(mandatory_node_roles=["health_checker"])
        evaluator = SwarmEvaluator(config)
        wf = _make_simple_workflow()
        result = evaluator.evaluate(wf, "/tmp/test", ["t1"])
        assert result.score == 0.0
        assert result.details.get("rejected") == "mandatory_component_missing"

    def test_mandatory_component_passes(self) -> None:
        config = _make_config(mandatory_node_roles=["builder"])

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(score=0.0, benchmark_score=0.5, hygiene_score=0.5)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        wf = _make_simple_workflow()
        result = evaluator.evaluate(wf, "/tmp/test", ["t1"])
        assert result.score > 0

    def test_frozen_node_rejection(self) -> None:
        config = _make_config(frozen_node_ids=["missing_node"])
        evaluator = SwarmEvaluator(config)
        wf = _make_simple_workflow()
        result = evaluator.evaluate(wf, "/tmp/test", ["t1"])
        assert result.score == 0.0
        assert result.details.get("rejected") == "frozen_node_violated"

    def test_frozen_node_passes(self) -> None:
        config = _make_config(frozen_node_ids=["study"])

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(score=0.0, benchmark_score=0.6, hygiene_score=0.7)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        wf = _make_simple_workflow()
        result = evaluator.evaluate(wf, "/tmp/test", ["t1"])
        assert result.score > 0

    def test_evaluate_batch(self) -> None:
        config = _make_config()

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(score=0.0, benchmark_score=0.5, hygiene_score=0.5)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        wf1 = _make_simple_workflow("wf1")
        wf2 = _make_simple_workflow("wf2")
        results = evaluator.evaluate_batch([wf1, wf2], "/tmp/test", ["t1"])
        assert len(results) == 2
        assert all(r.score > 0 for r in results)

    def test_multi_metric_composition(self) -> None:
        config = _make_config()

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(
                score=0.0, benchmark_score=1.0, hygiene_score=1.0,
                cost_usd=0.0, complexity=0.0,
            )

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        wf = _make_simple_workflow()
        result = evaluator.evaluate(wf, "/tmp/test", ["t1"])
        # 0.6*1.0 + 0.2*1.0 + 0.1*(1-0) + 0.1*(1-0) = 1.0
        assert result.score == 1.0

    def test_no_evaluator_fn(self) -> None:
        config = _make_config()
        evaluator = SwarmEvaluator(config)
        wf = _make_simple_workflow()
        result = evaluator.evaluate(wf, "/tmp/test", ["t1"])
        assert result.details.get("note") == "no_evaluator_fn_configured"
