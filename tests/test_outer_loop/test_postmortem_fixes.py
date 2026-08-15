"""Tests for outer loop v1 post-mortem fixes (issue #1272).

Covers all 10 fixes across 3 phases:
  P0: Evaluation integrity (Fixes 1-3)
  P1: Core differentiation (Fixes 4-6)
  P2: Performance & completeness (Fixes 7-10)
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from factory.outer_loop.designer import DesignerAgent, populate_prompt
from factory.outer_loop.engine import SwarmEngine, _extract_instance_results
from factory.outer_loop.evaluator import SwarmEvaluator
from factory.outer_loop.models import (
    AuditResult,
    EvalResult,
    GenerationSummary,
    Individual,
    MutationRecord,
    MutationType,
    SwarmConfig,
)
from factory.outer_loop.mutations import (
    WeightedRandomStrategy,
    _crossover_prompts,
    _extract_frozen_segments,
    _validate_frozen_segments,
    _validate_length,
    apply_random_mutation,
    prompt_mutate,
)
from factory.outer_loop.overfit import CONSECUTIVE_OVERFIT_LIMIT, OverfitDetector
from factory.outer_loop.subset import CalibratedSubsetSelector, FixedSubsetSelector
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
        "training_instances": ["t1", "t2", "t3"],
        "holdout_instances": ["h1", "h2"],
    }
    defaults.update(overrides)
    return SwarmConfig(**defaults)  # type: ignore[arg-type]


def _make_workflow() -> Workflow:
    return Workflow(
        name="test_wf",
        nodes={
            "researcher": AgentNode(
                id="researcher",
                role=AgentRole.RESEARCHER,
                prompt_template=(
                    "Study the codebase at /tmp/testbed. Read the issue at "
                    "/tmp/testbed/task-instruction.md. Explore the repository structure "
                    "and identify relevant files. MUST NOT modify tests."
                ),
                writes={".factory/research.md"},
            ),
            "builder": AgentNode(
                id="builder",
                role=AgentRole.BUILDER,
                prompt_template=(
                    "Read the task description at /tmp/testbed/task-instruction.md. "
                    "Implement the fix in the codebase at /tmp/testbed. Run pytest to "
                    "verify the changes work correctly. MUST commit changes."
                ),
                reads={".factory/research.md"},
            ),
            "gate": GateNode(id="gate", evaluator_type="fn"),
        },
        edges=[
            Edge(source="researcher", target="builder"),
            Edge(source="builder", target="gate"),
        ],
        start_node="researcher",
    )


# ── Fix #1: Web search blocking ─────────────────────────────────


class TestFix1WebSearchBlocking:
    def test_disallowed_tools_in_agent_invocation(self) -> None:
        """Verify --disallowedTools flag is present in the subprocess command."""
        from factory.outer_loop.direct_evaluator import DirectFeatureBenchEvaluator

        evaluator = DirectFeatureBenchEvaluator()
        wf = Workflow(
            name="test",
            nodes={
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    prompt_template="do the thing",
                ),
            },
            edges=[],
            start_node="builder",
        )

        import subprocess
        from pathlib import Path

        calls: list[list[str]] = []
        original_run = subprocess.run

        def capture_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            if args and isinstance(args[0], list) and "factory" in str(args[0]):
                calls.append(list(args[0]))
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        testbed = Path("/tmp/test-fb-websearch")
        testbed.mkdir(exist_ok=True)
        (testbed / ".factory").mkdir(exist_ok=True)
        (testbed / ".factory" / "reviews").mkdir(exist_ok=True)

        with patch.object(subprocess, "run", side_effect=capture_run):
            evaluator._run_workflow_agents(wf, testbed)

        assert len(calls) >= 1
        cmd = calls[0]
        assert "--disallowedTools" in cmd
        idx = cmd.index("--disallowedTools")
        assert cmd[idx + 1] == "WebSearch,WebFetch"

    def test_network_none_in_verify_docker(self) -> None:
        """Verify --network none is in the docker create command for verification."""
        from factory.outer_loop import direct_evaluator
        import inspect

        source = inspect.getsource(direct_evaluator.DirectFeatureBenchEvaluator._verify_in_docker)
        assert '"--network", "none"' in source or "'--network', 'none'" in source


# ── Fix #2: Raw pass rate fitness ────────────────────────────────


class TestFix2RawPassRate:
    def test_score_equals_benchmark_score(self) -> None:
        config = _make_config()

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(
                score=0.0, benchmark_score=0.65, hygiene_score=0.9,
                cost_usd=1.0, complexity=5.0,
            )

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        wf = _make_workflow()
        result = evaluator.evaluate(wf, "/tmp", ["t1"])
        assert result.score == 0.65

    def test_no_constant_offset(self) -> None:
        config = _make_config()

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(score=0.0, benchmark_score=0.0, hygiene_score=0.0)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        wf = _make_workflow()
        result = evaluator.evaluate(wf, "/tmp", ["t1"])
        assert result.score == 0.0

    def test_perfect_score(self) -> None:
        config = _make_config()

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(score=0.0, benchmark_score=1.0, hygiene_score=0.5)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        wf = _make_workflow()
        result = evaluator.evaluate(wf, "/tmp", ["t1"])
        assert result.score == 1.0


# ── Fix #3: Holdout every generation ─────────────────────────────


class TestFix3HoldoutEveryGeneration:
    def test_audit_generation_records_history(self) -> None:
        detector = OverfitDetector(threshold=0.15)
        detector.audit_generation(0, 0.8, 0.75)
        detector.audit_generation(1, 0.85, 0.78)
        detector.audit_generation(2, 0.9, 0.80)

        assert len(detector.history) == 3
        assert detector.history[0] == (0, 0.8, 0.75)
        assert detector.history[2] == (2, 0.9, 0.80)

    def test_audit_generation_returns_audit_result(self) -> None:
        detector = OverfitDetector(threshold=0.15)
        result = detector.audit_generation(0, 0.8, 0.7)

        assert isinstance(result, AuditResult)
        assert result.training_score == 0.8
        assert result.holdout_score == 0.7
        assert result.delta == pytest.approx(0.125)
        assert not result.overfit_flag

    def test_audit_generation_detects_overfit(self) -> None:
        detector = OverfitDetector(threshold=0.15)
        result = detector.audit_generation(0, 1.0, 0.5)

        assert result.overfit_flag
        assert result.delta == 0.5

    def test_early_stop_after_consecutive_overfit(self) -> None:
        detector = OverfitDetector(threshold=0.15)

        for i in range(CONSECUTIVE_OVERFIT_LIMIT):
            detector.audit_generation(i, 1.0, 0.5)

        assert detector.should_early_stop()

    def test_no_early_stop_without_consecutive_overfit(self) -> None:
        detector = OverfitDetector(threshold=0.15)
        detector.audit_generation(0, 1.0, 0.5)
        detector.audit_generation(1, 1.0, 0.9)
        detector.audit_generation(2, 1.0, 0.5)

        assert not detector.should_early_stop()

    def test_generation_summary_has_overfit_delta(self) -> None:
        config = _make_config(budget=30, population_size=2)

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            score = 0.7 if "h" not in instances[0] else 0.6
            return EvalResult(score=0.0, benchmark_score=score)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()
        pop = engine.seed(wf)
        summary = engine.evolve_generation(pop, 0)

        assert summary.overfit_delta is not None
        assert summary.holdout_score > 0

    def test_zero_training_score_no_crash(self) -> None:
        detector = OverfitDetector(threshold=0.15)
        result = detector.audit_generation(0, 0.0, 0.0)

        assert result.delta == 0.0
        assert not result.overfit_flag


# ── Fix #4: Calibrated subset selector ───────────────────────────


class TestFix4CalibratedSubsetSelector:
    def test_calibrate_selects_difficulty_range(self) -> None:
        selector = CalibratedSubsetSelector(
            training_size=3,
            holdout_size=2,
            difficulty_range=(0.3, 0.7),
        )

        all_instances = [f"inst_{i}" for i in range(10)]
        # Use exact floats to avoid floating-point boundary issues (e.g. 7*0.1 > 0.7)
        scores = {f"inst_{i}": round(i * 0.1, 1) for i in range(10)}

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            s = scores.get(instances[0], 0.0)
            return EvalResult(score=s, benchmark_score=s)

        wf = _make_workflow()
        result = selector.calibrate(all_instances, wf, mock_eval)

        assert selector.is_calibrated
        assert len(selector.training_instances) == 3
        assert len(selector.holdout_instances) == 2

        overlap = set(selector.training_instances) & set(selector.holdout_instances)
        assert len(overlap) == 0

    def test_calibrate_widens_range_if_insufficient(self) -> None:
        selector = CalibratedSubsetSelector(
            training_size=3,
            holdout_size=2,
            difficulty_range=(0.45, 0.55),
        )

        all_instances = [f"inst_{i}" for i in range(10)]

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(score=0.0, benchmark_score=0.4)

        wf = _make_workflow()
        selector.calibrate(all_instances, wf, mock_eval)

        assert selector.is_calibrated
        assert len(selector.training_instances) >= 1

    def test_select_after_calibration(self) -> None:
        selector = CalibratedSubsetSelector(training_size=3, holdout_size=2)

        all_instances = [f"inst_{i}" for i in range(10)]

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(score=0.0, benchmark_score=0.5)

        wf = _make_workflow()
        selector.calibrate(all_instances, wf, mock_eval)
        selected = selector.select(all_instances, generation=0, budget_remaining=100)

        assert selected == selector.training_instances

    def test_select_before_calibration(self) -> None:
        selector = CalibratedSubsetSelector(training_size=3)
        result = selector.select(["a", "b", "c", "d"], generation=0, budget_remaining=100)
        assert result == ["a", "b", "c"]

    def test_protocol_conformance(self) -> None:
        from factory.outer_loop.subset import SubsetSelector

        selector = CalibratedSubsetSelector()
        assert isinstance(selector, SubsetSelector)

    def test_swarm_config_has_difficulty_range(self) -> None:
        config = _make_config()
        assert config.difficulty_range == (0.3, 0.7)
        assert config.training_size == 10
        assert config.holdout_size == 5


# ── Fix #5: Designer prompts ─────────────────────────────────────


class TestFix5DesignerPrompts:
    def test_populate_prompt_researcher(self) -> None:
        prompt = populate_prompt("researcher", "featurebench")
        assert "/tmp/testbed" in prompt
        assert "task-instruction.md" in prompt

    def test_populate_prompt_builder(self) -> None:
        prompt = populate_prompt("builder", "featurebench")
        assert "Implement" in prompt
        assert "task-instruction.md" in prompt

    def test_populate_prompt_unknown_role(self) -> None:
        prompt = populate_prompt("unknown_role", "featurebench")
        assert "unknown_role" in prompt

    def test_design_minimal_has_prompts(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_minimal("featurebench")

        for node in wf.nodes.values():
            if hasattr(node, "role") and hasattr(node, "prompt_template"):
                prompt = node.prompt_template  # type: ignore[union-attr]
                assert prompt, f"Node {node.id} has empty prompt"  # type: ignore[union-attr]
                assert "testbed" in prompt or "task" in prompt

    def test_design_thorough_has_prompts(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_thorough("featurebench")

        agent_nodes = [
            n for n in wf.nodes.values()
            if hasattr(n, "prompt_template") and hasattr(n, "role")
        ]
        for node in agent_nodes:
            prompt = node.prompt_template  # type: ignore[union-attr]
            assert prompt, f"Node {node.id} has empty prompt"  # type: ignore[union-attr]

    def test_design_custom_has_prompts(self) -> None:
        designer = DesignerAgent()
        wf = designer.design_custom("featurebench", {"max_nodes": 5})

        agent_nodes = [
            n for n in wf.nodes.values()
            if hasattr(n, "prompt_template") and hasattr(n, "role")
        ]
        for node in agent_nodes:
            prompt = node.prompt_template  # type: ignore[union-attr]
            assert prompt, f"Node {node.id} has empty prompt"  # type: ignore[union-attr]


# ── Fix #6: PROMPT_MUTATE operator ───────────────────────────────


class TestFix6PromptMutate:
    def test_prompt_mutate_enum_exists(self) -> None:
        assert MutationType.PROMPT_MUTATE.value == "prompt_mutate"

    def test_prompt_mutate_in_weights(self) -> None:
        strategy = WeightedRandomStrategy()
        weights = strategy.get_operator_weights()
        assert MutationType.PROMPT_MUTATE.value in weights
        assert weights[MutationType.PROMPT_MUTATE.value] == pytest.approx(0.15)

    def test_prompt_mutate_operator(self) -> None:
        wf = _make_workflow()
        result = prompt_mutate(wf, ["researcher"])
        assert result is not None
        mutated_wf, rec = result
        assert rec.operator == MutationType.PROMPT_MUTATE

    def test_prompt_mutate_preserves_frozen_segments(self) -> None:
        wf = _make_workflow()
        original_prompt = wf.nodes["researcher"].prompt_template  # type: ignore[union-attr]
        frozen = _extract_frozen_segments(original_prompt)

        result = prompt_mutate(wf, ["researcher"])
        if result is not None:
            mutated_wf, _ = result
            new_prompt = mutated_wf.nodes["researcher"].prompt_template  # type: ignore[union-attr]
            for seg in frozen:
                assert seg in new_prompt

    def test_prompt_mutate_skips_frozen_nodes(self) -> None:
        wf = _make_workflow()
        result = prompt_mutate(wf, ["researcher"], frozen_nodes={"researcher"})
        assert result is None

    def test_prompt_mutate_with_archive_prompts(self) -> None:
        wf = _make_workflow()
        archive_prompts = {
            "researcher": (
                "Analyze the issue at /tmp/testbed/task-instruction.md carefully. "
                "Read all source files in the repository. Explore the directory "
                "structure and identify relevant modules. MUST NOT modify tests."
            ),
        }
        result = prompt_mutate(
            wf, ["researcher"], archive_best_prompts=archive_prompts,
        )
        assert result is not None

    def test_extract_frozen_segments(self) -> None:
        text = "Do the task. MUST NOT delete tests. You MUST commit. NEVER skip QA."
        segments = _extract_frozen_segments(text)
        assert len(segments) >= 2

    def test_validate_length_within_bounds(self) -> None:
        assert _validate_length("x" * 100, "x" * 100)
        assert _validate_length("x" * 90, "x" * 100)
        assert _validate_length("x" * 110, "x" * 100)

    def test_validate_length_out_of_bounds(self) -> None:
        assert not _validate_length("x" * 50, "x" * 100)
        assert not _validate_length("x" * 150, "x" * 100)

    def test_validate_frozen_segments_pass(self) -> None:
        assert _validate_frozen_segments(
            "Do something. MUST NOT delete tests.", ["MUST NOT delete tests"]
        )

    def test_validate_frozen_segments_fail(self) -> None:
        assert not _validate_frozen_segments(
            "Do something else.", ["MUST NOT delete tests"]
        )

    def test_crossover_prompts_basic(self) -> None:
        result = _crossover_prompts(
            "Study the code. Find bugs. Fix them.",
            "Analyze the repo. Identify issues. Resolve them.",
            "researcher",
        )
        assert len(result) > 0
        assert result.endswith(".")

    def test_crossover_prompts_empty_current(self) -> None:
        result = _crossover_prompts("", "donor prompt here.", "builder")
        assert result == "donor prompt here."

    def test_crossover_prompts_empty_donor(self) -> None:
        result = _crossover_prompts("current prompt here.", "", "builder")
        assert result == "current prompt here."

    def test_try_mutation_selects_prompt_mutate(self) -> None:
        wf = _make_workflow()
        weights = {t.value: (1.0 if t == MutationType.PROMPT_MUTATE else 0.0) for t in MutationType}
        strategy = WeightedRandomStrategy(weights=weights)
        result = apply_random_mutation(wf, strategy, generation=0, max_attempts=20)
        if result is not None:
            _, rec = result
            assert rec.operator == MutationType.PROMPT_MUTATE


# ── Fix #7: Parallel evaluation ──────────────────────────────────


class TestFix7ParallelEvaluation:
    def test_evaluate_batch_parallel(self) -> None:
        config = _make_config()
        call_count: dict[str, int] = {"n": 0}

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            call_count["n"] += 1
            return EvalResult(score=0.0, benchmark_score=0.5)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        wfs = [_make_workflow() for _ in range(4)]

        for i, wf in enumerate(wfs):
            wf.name = f"wf_{i}"

        results = evaluator.evaluate_batch(wfs, "/tmp", ["t1"], parallelism=4)
        assert len(results) == 4

    def test_evaluate_batch_sequential_fallback(self) -> None:
        config = _make_config()

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(score=0.0, benchmark_score=0.5)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        wf = _make_workflow()

        results = evaluator.evaluate_batch([wf], "/tmp", ["t1"], parallelism=4)
        assert len(results) == 1

    def test_swarm_config_has_parallelism(self) -> None:
        config = _make_config()
        assert config.parallelism == 4

    def test_parallel_eval_handles_errors(self) -> None:
        config = _make_config()
        call_count: dict[str, int] = {"n": 0}

        def flaky_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("eval failed")
            return EvalResult(score=0.0, benchmark_score=0.5)

        evaluator = SwarmEvaluator(config, evaluator_fn=flaky_eval)
        wfs = [_make_workflow() for _ in range(3)]
        for i, wf in enumerate(wfs):
            wf.name = f"wf_{i}"

        results = evaluator.evaluate_batch(wfs, "/tmp", ["t1"], parallelism=3)
        assert len(results) == 3
        assert any(r.score == 0.0 and r.details.get("error") for r in results)


# ── Fix #8: Clean generation lifecycle ───────────────────────────


class TestFix8CleanLifecycle:
    def test_evolve_generation_summary_complete(self) -> None:
        config = _make_config(budget=50, population_size=2)

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(score=0.0, benchmark_score=0.5)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()
        pop = engine.seed(wf)

        summary = engine.evolve_generation(pop, 0)

        assert summary.generation == 0
        assert summary.population_size > 0
        assert summary.best_score >= 0
        assert summary.mean_score >= 0
        assert summary.diversity >= 0
        assert summary.holdout_score >= 0
        assert summary.overfit_delta is not None
        assert summary.hyperparameters is not None

    def test_engine_has_private_methods(self) -> None:
        config = _make_config()

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(score=0.0, benchmark_score=0.5)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        engine = SwarmEngine(config, evaluator)

        assert hasattr(engine, "_evaluate_population")
        assert hasattr(engine, "_evaluate_holdout")
        assert hasattr(engine, "_select_and_mutate")
        assert hasattr(engine, "_log_generation")

    def test_overfit_early_stop_terminates_run(self) -> None:
        config = _make_config(budget=100, population_size=2)

        call_count: dict[str, int] = {"n": 0}

        def overfit_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            call_count["n"] += 1
            if any("h" in i for i in instances):
                return EvalResult(score=0.0, benchmark_score=0.1)
            return EvalResult(score=0.0, benchmark_score=0.9)

        evaluator = SwarmEvaluator(config, evaluator_fn=overfit_eval)
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()

        result = engine.run(wf)
        assert result.convergence_reason == "overfitting"


# ── Fix #9: Per-instance tracking ────────────────────────────────


class TestFix9PerInstanceTracking:
    def test_individual_has_instance_results(self) -> None:
        ind = Individual(
            id="test",
            workflow_data={},
            instance_results={"t1": True, "t2": False, "t3": True},
        )
        assert ind.instance_results["t1"] is True
        assert ind.instance_results["t2"] is False

    def test_per_instance_summary(self) -> None:
        ind = Individual(
            id="test",
            workflow_data={},
            instance_results={"t1": True, "t2": False, "t3": True},
        )
        summary = ind.per_instance_summary()
        assert summary["passed"] == 2
        assert summary["failed"] == 1
        assert summary["total"] == 3

    def test_per_instance_summary_empty(self) -> None:
        ind = Individual(id="test", workflow_data={})
        summary = ind.per_instance_summary()
        assert summary == {"passed": 0, "failed": 0, "total": 0}

    def test_extract_instance_results(self) -> None:
        result = EvalResult(
            score=0.5,
            benchmark_score=0.5,
            details={"instances": {"t1": {"resolved": True}, "t2": {"resolved": False}}},
        )
        extracted = _extract_instance_results(result)
        assert extracted == {"t1": True, "t2": False}

    def test_extract_instance_results_no_details(self) -> None:
        result = EvalResult(score=0.5, benchmark_score=0.5)
        extracted = _extract_instance_results(result)
        assert extracted == {}

    def test_instance_results_serialization(self) -> None:
        ind = Individual(
            id="test",
            workflow_data={},
            instance_results={"t1": True, "t2": False},
        )
        data = ind.model_dump(mode="json")
        restored = Individual.model_validate(data)
        assert restored.instance_results == {"t1": True, "t2": False}

    def test_generation_summary_overfit_delta(self) -> None:
        summary = GenerationSummary(
            generation=0,
            population_size=4,
            best_score=0.8,
            mean_score=0.5,
            diversity=0.3,
            holdout_score=0.7,
            overfit_delta=0.125,
        )
        assert summary.overfit_delta == 0.125


# ── Fix #10: Functional INSERT_NODE prompts ──────────────────────


class TestFix10InsertNodePrompts:
    def test_insert_node_has_prompt(self) -> None:
        wf = _make_workflow()
        weights = {t.value: (1.0 if t == MutationType.NODE_INSERT else 0.0) for t in MutationType}
        strategy = WeightedRandomStrategy(weights=weights)

        success = False
        for _ in range(20):
            result = apply_random_mutation(wf, strategy, generation=0, max_attempts=5)
            if result is not None:
                mutated_wf, rec = result
                if rec.operator == MutationType.NODE_INSERT and rec.target_node:
                    new_node = mutated_wf.nodes.get(rec.target_node)
                    if new_node and hasattr(new_node, "prompt_template"):
                        assert new_node.prompt_template, f"Node {rec.target_node} has empty prompt"  # type: ignore[union-attr]
                        success = True
                        break

        assert success, "No successful NODE_INSERT mutation in 20 attempts"

    def test_insert_node_role_selection(self) -> None:
        """Inserted nodes choose roles based on surrounding topology."""
        wf = _make_workflow()
        weights = {t.value: (1.0 if t == MutationType.NODE_INSERT else 0.0) for t in MutationType}
        strategy = WeightedRandomStrategy(weights=weights)

        roles_seen: set[str] = set()
        for _ in range(50):
            result = apply_random_mutation(wf, strategy, generation=0, max_attempts=5)
            if result is not None:
                mutated_wf, rec = result
                if rec.operator == MutationType.NODE_INSERT and rec.target_node:
                    new_node = mutated_wf.nodes.get(rec.target_node)
                    if new_node and hasattr(new_node, "role"):
                        roles_seen.add(new_node.role.value)  # type: ignore[union-attr]

        assert len(roles_seen) >= 1


# ── Integration tests ────────────────────────────────────────────


class TestIntegration:
    def test_full_run_with_all_fixes(self) -> None:
        """Integration: run 2 generations with all fixes active."""
        config = _make_config(
            budget=30,
            population_size=2,
            training_instances=["t1", "t2"],
            holdout_instances=["h1"],
        )

        eval_counter: dict[str, int] = {"n": 0}

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            eval_counter["n"] += 1
            score = min(0.3 + eval_counter["n"] * 0.02, 1.0)
            return EvalResult(
                score=0.0,
                benchmark_score=score,
                details={"instances": {i: {"resolved": score > 0.5} for i in instances}},
            )

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()

        result = engine.run(wf)

        assert result.generations_completed >= 1
        assert result.total_evaluations > 0
        assert len(result.trajectory) >= 1

        for summary in result.trajectory:
            assert summary.holdout_score >= 0
            assert summary.overfit_delta is not None
            assert summary.hyperparameters is not None

    def test_designer_workflows_are_functional(self) -> None:
        """All designer workflows should have non-empty prompts."""
        designer = DesignerAgent()

        for method_name in ["design_minimal", "design_thorough"]:
            method = getattr(designer, method_name)
            wf = method("featurebench")

            for node_id, node in wf.nodes.items():
                if hasattr(node, "prompt_template") and hasattr(node, "role"):
                    prompt = node.prompt_template  # type: ignore[union-attr]
                    assert prompt, f"{method_name}: {node_id} has empty prompt"

    def test_mutation_weights_sum_to_one(self) -> None:
        strategy = WeightedRandomStrategy()
        weights = strategy.get_operator_weights()
        total = sum(weights.values())
        assert total == pytest.approx(1.0, abs=0.01)


# ── Fix #7 addendum: evaluate_batch wired into engine ──────────


class TestFix7EngineParallelWiring:
    def test_parallel_path_used_when_parallelism_gt_1(self) -> None:
        """Engine._evaluate_population uses evaluate_batch when parallelism > 1."""
        config = _make_config(budget=50, population_size=3, parallelism=4)
        batch_calls: list[int] = []

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(score=0.0, benchmark_score=0.5)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        original_batch = evaluator.evaluate_batch

        def tracking_batch(
            workflows: list[Workflow],
            project_dir: str,
            instances: list[str],
            parallelism: int = 1,
        ) -> list[EvalResult]:
            batch_calls.append(len(workflows))
            return original_batch(workflows, project_dir, instances, parallelism=parallelism)

        evaluator.evaluate_batch = tracking_batch  # type: ignore[method-assign]
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()
        pop = engine.seed(wf)

        engine._evaluate_population(pop, ["t1"], "/tmp")
        assert len(batch_calls) >= 1

    def test_sequential_path_used_when_parallelism_1(self) -> None:
        """Engine._evaluate_population uses sequential evaluate when parallelism == 1."""
        config = _make_config(budget=50, population_size=3, parallelism=1)
        batch_calls: list[int] = []

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(score=0.0, benchmark_score=0.5)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        original_batch = evaluator.evaluate_batch

        def tracking_batch(
            workflows: list[Workflow],
            project_dir: str,
            instances: list[str],
            parallelism: int = 1,
        ) -> list[EvalResult]:
            batch_calls.append(len(workflows))
            return original_batch(workflows, project_dir, instances, parallelism=parallelism)

        evaluator.evaluate_batch = tracking_batch  # type: ignore[method-assign]
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()
        pop = engine.seed(wf)

        engine._evaluate_population(pop, ["t1"], "/tmp")
        assert len(batch_calls) == 0

    def test_parallel_eval_updates_scores(self) -> None:
        """Parallel path correctly updates individual scores and archive."""
        config = _make_config(budget=50, population_size=3, parallelism=4)

        counter: dict[str, int] = {"n": 0}

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            counter["n"] += 1
            return EvalResult(score=0.0, benchmark_score=0.5 + counter["n"] * 0.01)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        engine = SwarmEngine(config, evaluator)
        wf = _make_workflow()
        pop = engine.seed(wf)

        engine._evaluate_population(pop, ["t1"], "/tmp")

        scored = [ind for ind in pop.individuals if ind.score > 0]
        assert len(scored) > 0
        assert engine.archive.size > 0


# ── Fix #6 addendum: PROMPT_MUTATE short prompt length ─────────


class TestFix6ShortPromptLength:
    def test_short_prompt_relaxed_lower_bound(self) -> None:
        """Short prompts (<100 chars) accept 50% of original length."""
        short_original = "Fix the bug."  # 12 chars
        # 50% of 12 = 6 chars, so 7 chars should pass
        assert _validate_length("x" * 7, short_original)

    def test_short_prompt_rejects_below_50pct(self) -> None:
        """Short prompts (<100 chars) still reject below 50%."""
        short_original = "Fix the bug."  # 12 chars
        # 50% of 12 = 6, so 5 chars should fail
        assert not _validate_length("x" * 5, short_original)

    def test_short_prompt_allows_growth_via_crossover(self) -> None:
        """Short prompts can grow significantly through donor crossover."""
        short_original = "x" * 50  # 50 chars < 100
        # 120% of 50 = 60, upper bound still enforced
        assert _validate_length("x" * 60, short_original)
        assert not _validate_length("x" * 61, short_original)

    def test_long_prompt_still_uses_80pct_bound(self) -> None:
        """Prompts >= 100 chars use the original 80% lower bound."""
        long_original = "x" * 200
        # 80% of 200 = 160
        assert _validate_length("x" * 160, long_original)
        assert not _validate_length("x" * 159, long_original)

    def test_boundary_100_chars_uses_strict_bound(self) -> None:
        """Exactly 100 chars uses the strict 80% lower bound."""
        original = "x" * 100
        assert _validate_length("x" * 80, original)
        assert not _validate_length("x" * 79, original)

    def test_boundary_99_chars_uses_relaxed_bound(self) -> None:
        """99 chars (< 100) uses the relaxed 50% lower bound."""
        original = "x" * 99
        # 50% of 99 = 49.5
        assert _validate_length("x" * 50, original)
        assert not _validate_length("x" * 49, original)
