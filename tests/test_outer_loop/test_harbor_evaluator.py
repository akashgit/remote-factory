"""Tests for HarborEvaluator, create_seed_workflow, and workflow_to_harbor_yaml."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import yaml

from factory.outer_loop.harbor_evaluator import (
    HarborEvaluator,
    create_seed_workflow,
    workflow_to_harbor_yaml,
)
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    GateNode,
    Workflow,
)


class TestCreateSeedWorkflow:
    def test_returns_valid_workflow(self) -> None:
        wf = create_seed_workflow()
        assert isinstance(wf, Workflow)
        assert wf.name == "featurebench-seed"

    def test_has_four_nodes(self) -> None:
        wf = create_seed_workflow()
        assert len(wf.nodes) == 4
        assert set(wf.nodes.keys()) == {"researcher", "builder", "health_checker", "gate"}

    def test_has_correct_edges(self) -> None:
        wf = create_seed_workflow()
        edge_pairs = [(e.source, e.target) for e in wf.edges]
        assert ("researcher", "builder") in edge_pairs
        assert ("builder", "health_checker") in edge_pairs
        assert ("health_checker", "gate") in edge_pairs

    def test_start_node_is_researcher(self) -> None:
        wf = create_seed_workflow()
        assert wf.start_node == "researcher"

    def test_builder_has_prompt(self) -> None:
        wf = create_seed_workflow()
        builder = wf.nodes["builder"]
        assert isinstance(builder, AgentNode)
        assert builder.prompt_template
        assert "task-instruction" in builder.prompt_template

    def test_roundtrip_serialization(self) -> None:
        wf = create_seed_workflow()
        d = wf.to_dict()
        restored = Workflow.from_dict(d)
        assert restored.name == wf.name
        assert set(restored.nodes.keys()) == set(wf.nodes.keys())
        assert len(restored.edges) == len(wf.edges)

    def test_node_roles(self) -> None:
        wf = create_seed_workflow()
        assert wf.nodes["researcher"].role == AgentRole.RESEARCHER  # type: ignore[union-attr]
        assert wf.nodes["builder"].role == AgentRole.BUILDER  # type: ignore[union-attr]
        assert wf.nodes["health_checker"].role == AgentRole.HEALTH_CHECKER  # type: ignore[union-attr]
        assert isinstance(wf.nodes["gate"], GateNode)


class TestWorkflowToHarborYaml:
    def test_produces_valid_yaml(self) -> None:
        wf = create_seed_workflow()
        result = workflow_to_harbor_yaml(wf)
        parsed = yaml.safe_load(result)
        assert isinstance(parsed, dict)

    def test_includes_agent_nodes_with_prompts(self) -> None:
        wf = create_seed_workflow()
        result = workflow_to_harbor_yaml(wf)
        parsed = yaml.safe_load(result)
        assert "builder" in parsed
        assert "task_prompt_builder" in parsed["builder"]["slots"]

    def test_includes_timeout(self) -> None:
        wf = create_seed_workflow()
        result = workflow_to_harbor_yaml(wf)
        parsed = yaml.safe_load(result)
        builder_slots = parsed["builder"]["slots"]
        assert "timeout_builder" in builder_slots
        assert builder_slots["timeout_builder"] == 7200

    def test_gate_without_gate_prompt_excluded(self) -> None:
        wf = create_seed_workflow()
        result = workflow_to_harbor_yaml(wf)
        parsed = yaml.safe_load(result)
        assert "gate" not in parsed

    def test_gate_with_prompt_included(self) -> None:
        wf = Workflow(
            name="test",
            nodes={
                "g": GateNode(
                    id="g",
                    evaluator_type="fn",
                    gate_prompt="Check if tests pass",
                ),
            },
            edges=[],
            start_node="g",
        )
        result = workflow_to_harbor_yaml(wf)
        parsed = yaml.safe_load(result)
        assert "g" in parsed
        assert "gate_prompt_g" in parsed["g"]["slots"]

    def test_empty_prompt_excluded(self) -> None:
        wf = Workflow(
            name="test",
            nodes={
                "b": AgentNode(id="b", role=AgentRole.BUILDER, prompt_template=""),
            },
            edges=[],
            start_node="b",
        )
        result = workflow_to_harbor_yaml(wf)
        parsed = yaml.safe_load(result)
        assert parsed is None or "b" not in (parsed or {})


class TestHarborEvaluator:
    def test_missing_script_returns_zero(self, tmp_path: Path) -> None:
        evaluator = HarborEvaluator(benchmarks_dir=tmp_path, timeout=60)
        wf = create_seed_workflow()
        result = evaluator(wf, "/tmp/test", ["instance1"])
        assert result.score == 0.0
        assert "error" in result.details

    def test_all_resolved(self, tmp_path: Path) -> None:
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash\necho 'Result: RESOLVED'\necho '\"cost_usd\": 1.5'")
        script.chmod(0o755)

        evaluator = HarborEvaluator(benchmarks_dir=tmp_path, timeout=60)
        wf = create_seed_workflow()
        result = evaluator(wf, "/tmp/test", ["i1", "i2"])
        assert result.score == 1.0
        assert result.benchmark_score == 1.0
        assert result.cost_usd == 3.0

    def test_partial_resolve(self, tmp_path: Path) -> None:
        call_count = 0

        def mock_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                stdout = "Result: RESOLVED\n\"cost_usd\": 1.0"
            else:
                stdout = "Result: NOT RESOLVED\n\"cost_usd\": 0.5"
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=stdout, stderr=""
            )

        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash\necho test")
        script.chmod(0o755)

        evaluator = HarborEvaluator(benchmarks_dir=tmp_path, timeout=60)
        wf = create_seed_workflow()

        with patch("subprocess.run", side_effect=mock_run):
            result = evaluator(wf, "/tmp/test", ["i1", "i2"])

        assert result.score == 0.5
        assert result.cost_usd == 1.5

    def test_timeout_scores_zero(self, tmp_path: Path) -> None:
        def mock_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd="test", timeout=60)

        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash\necho test")
        script.chmod(0o755)

        evaluator = HarborEvaluator(benchmarks_dir=tmp_path, timeout=60)
        wf = create_seed_workflow()

        with patch("subprocess.run", side_effect=mock_run):
            result = evaluator(wf, "/tmp/test", ["i1"])

        assert result.score == 0.0

    def test_complexity_from_node_count(self, tmp_path: Path) -> None:
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash\necho 'Result: RESOLVED'")
        script.chmod(0o755)

        evaluator = HarborEvaluator(benchmarks_dir=tmp_path, timeout=60)
        wf = create_seed_workflow()
        result = evaluator(wf, "/tmp/test", ["i1"])
        assert result.complexity == 4.0

    def test_passes_yaml_b64_to_env(self, tmp_path: Path) -> None:
        captured_env: dict[str, str] = {}

        def mock_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            env = kwargs.get("env", {})
            assert isinstance(env, dict)
            captured_env.update(env)
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="Result: NOT RESOLVED", stderr=""
            )

        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash\necho test")
        script.chmod(0o755)

        evaluator = HarborEvaluator(benchmarks_dir=tmp_path, timeout=60)
        wf = create_seed_workflow()

        with patch("subprocess.run", side_effect=mock_run):
            evaluator(wf, "/tmp/test", ["i1"])

        assert "FACTORY_WORKFLOW_YAML_B64" in captured_env

    def test_implements_evaluator_fn_protocol(self) -> None:
        from factory.outer_loop.evaluator import EvaluatorFn

        evaluator = HarborEvaluator(timeout=60)
        assert isinstance(evaluator, EvaluatorFn)


class TestRunEvolution:
    def test_main_missing_training_instances(self) -> None:
        from factory.outer_loop.run_evolution import main

        result = main(["--training-instances", ""])
        assert result == 1

    def test_main_parses_instances(self) -> None:
        from factory.outer_loop.run_evolution import main

        with patch(
            "factory.outer_loop.run_evolution.SwarmEngine"
        ) as mock_engine_cls:
            mock_engine = mock_engine_cls.return_value
            from factory.outer_loop.models import OuterLoopResult

            mock_engine.run.return_value = OuterLoopResult(
                convergence_reason="budget_exhausted",
            )
            result = main([
                "--training-instances", "a,b,c",
                "--holdout-instances", "d,e",
                "--budget", "1",
                "--population", "2",
            ])
            assert result == 0
            mock_engine.run.assert_called_once()
