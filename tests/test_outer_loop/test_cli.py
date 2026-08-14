"""Tests for outer-loop CLI argument parsing and mode registration."""

from __future__ import annotations

import pytest


class TestOuterLoopModeRegistration:
    def test_outer_loop_in_ceo_modes(self) -> None:
        from factory.cli._helpers import CEO_MODES

        assert "outer-loop" in CEO_MODES

    def test_outer_loop_in_run_modes(self) -> None:
        from factory.cli._helpers import RUN_MODES

        assert "outer-loop" in RUN_MODES

    def test_outer_loop_workflow_registered(self) -> None:
        from factory.workflow.definitions import _get_builtin_registry

        registry = _get_builtin_registry()
        assert "outer-loop" in registry


class TestOuterLoopCLIParsing:
    def _parse_ceo(self, *args: str) -> object:
        from factory.cli._main import build_parser

        parser = build_parser()
        return parser.parse_args(["ceo", *args])

    def test_mode_outer_loop_accepted(self) -> None:
        ns = self._parse_ceo("/tmp/project", "--mode", "outer-loop")
        assert ns.mode == "outer-loop"

    def test_benchmark_parsed(self) -> None:
        ns = self._parse_ceo(
            "/tmp/project", "--mode", "outer-loop",
            "--benchmark", "featurebench",
        )
        assert ns.benchmark == "featurebench"

    def test_budget_parsed(self) -> None:
        ns = self._parse_ceo(
            "/tmp/project", "--mode", "outer-loop",
            "--budget", "50",
        )
        assert ns.ol_budget == 50

    def test_population_parsed(self) -> None:
        ns = self._parse_ceo(
            "/tmp/project", "--mode", "outer-loop",
            "--population", "8",
        )
        assert ns.population == 8

    def test_target_score_parsed(self) -> None:
        ns = self._parse_ceo(
            "/tmp/project", "--mode", "outer-loop",
            "--target-score", "0.85",
        )
        assert ns.target_score == pytest.approx(0.85)

    def test_seed_mode_parsed(self) -> None:
        ns = self._parse_ceo(
            "/tmp/project", "--mode", "outer-loop",
            "--seed", "improve",
        )
        assert ns.seed_mode == "improve"

    def test_training_instances_parsed(self) -> None:
        ns = self._parse_ceo(
            "/tmp/project", "--mode", "outer-loop",
            "--training-instances", "fb-1,fb-2,fb-3",
        )
        assert ns.training_instances == "fb-1,fb-2,fb-3"

    def test_holdout_instances_parsed(self) -> None:
        ns = self._parse_ceo(
            "/tmp/project", "--mode", "outer-loop",
            "--holdout-instances", "fb-4,fb-5",
        )
        assert ns.holdout_instances == "fb-4,fb-5"

    def test_training_instances_as_list(self) -> None:
        """Verify comma-separated strings can be split into lists."""
        ns = self._parse_ceo(
            "/tmp/project", "--mode", "outer-loop",
            "--training-instances", "a,b,c",
        )
        instances = ns.training_instances.split(",")
        assert instances == ["a", "b", "c"]

    def test_defaults_when_not_specified(self) -> None:
        ns = self._parse_ceo("/tmp/project", "--mode", "outer-loop")
        assert ns.benchmark is None
        assert ns.ol_budget is None
        assert ns.population is None
        assert ns.target_score is None
        assert ns.seed_mode is None
        assert ns.training_instances is None
        assert ns.holdout_instances is None

    def test_all_args_together(self) -> None:
        ns = self._parse_ceo(
            "/tmp/project", "--mode", "outer-loop",
            "--benchmark", "terminalbench",
            "--budget", "100",
            "--population", "6",
            "--target-score", "0.9",
            "--seed", "evolve",
            "--training-instances", "t1,t2,t3,t4,t5",
            "--holdout-instances", "h1,h2",
        )
        assert ns.mode == "outer-loop"
        assert ns.benchmark == "terminalbench"
        assert ns.ol_budget == 100
        assert ns.population == 6
        assert ns.target_score == pytest.approx(0.9)
        assert ns.seed_mode == "evolve"
        assert ns.training_instances.split(",") == ["t1", "t2", "t3", "t4", "t5"]
        assert ns.holdout_instances.split(",") == ["h1", "h2"]


class TestOuterLoopWorkflowGraph:
    def test_workflow_validates(self) -> None:
        from factory.outer_loop.workflow import outer_loop_workflow

        wf = outer_loop_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Workflow validation issues: {issues}"

    def test_workflow_name(self) -> None:
        from factory.outer_loop.workflow import outer_loop_workflow

        wf = outer_loop_workflow()
        assert wf.name == "outer-loop"

    def test_workflow_start_node(self) -> None:
        from factory.outer_loop.workflow import outer_loop_workflow

        wf = outer_loop_workflow()
        assert wf.start_node == "study"

    def test_workflow_has_expected_nodes(self) -> None:
        from factory.outer_loop.workflow import outer_loop_workflow

        wf = outer_loop_workflow()
        expected = {
            "study", "seed_population", "evaluate_batch", "select",
            "mutate", "novelty_filter", "designer_agent", "gate_plateau",
            "holdout_audit", "export_best", "archivist",
        }
        assert set(wf.nodes.keys()) == expected

    def test_workflow_generation_loop(self) -> None:
        """gate_plateau has a PROCEED edge back to evaluate_batch (loop)."""
        from factory.outer_loop.workflow import outer_loop_workflow
        from factory.workflow.primitives import VerdictType

        wf = outer_loop_workflow()
        loop_edge = [
            e for e in wf.edges
            if e.source == "gate_plateau"
            and e.target == "evaluate_batch"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(loop_edge) == 1

    def test_workflow_exit_to_holdout(self) -> None:
        """gate_plateau HALT goes to holdout_audit."""
        from factory.outer_loop.workflow import outer_loop_workflow
        from factory.workflow.primitives import VerdictType

        wf = outer_loop_workflow()
        exit_edge = [
            e for e in wf.edges
            if e.source == "gate_plateau"
            and e.target == "holdout_audit"
            and e.condition == VerdictType.HALT
        ]
        assert len(exit_edge) == 1

    def test_workflow_serialization_round_trip(self) -> None:
        from factory.outer_loop.workflow import outer_loop_workflow
        from factory.workflow.primitives import Workflow

        wf = outer_loop_workflow()
        data = wf.to_dict()
        restored = Workflow.from_dict(data)

        assert restored.name == wf.name
        assert set(restored.nodes.keys()) == set(wf.nodes.keys())
        assert len(restored.edges) == len(wf.edges)
