"""Tests for the FeatureBench contributed workflow."""

from __future__ import annotations

from factory.models import ProjectState
from factory.workflow.contributed.featurebench import meta, workflow
from factory.workflow.definitions import register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    GateNode,
    VerdictType,
)


class TestFeaturebenchWorkflow:
    """Tests for featurebench workflow graph structure."""

    def test_workflow_name(self) -> None:
        wf = workflow()
        assert wf.name == "featurebench"

    def test_node_count(self) -> None:
        """Workflow has exactly 5 nodes: study, builder, gate_tests, health_checker, auto_merge."""
        wf = workflow()
        assert len(wf.nodes) == 5
        assert set(wf.nodes.keys()) == {
            "study", "builder", "gate_tests", "health_checker", "auto_merge",
        }

    def test_start_node(self) -> None:
        wf = workflow()
        assert wf.start_node == "study"

    def test_graph_validates(self) -> None:
        """Graph passes structural validation (DAG check, edge consistency)."""
        wf = workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Workflow has validation issues: {issues}"

    def test_edge_count(self) -> None:
        """5 edges: study->builder, builder->gate_tests, gate_tests->merge,
        gate_tests->health_checker RELOOP, health_checker->builder."""
        wf = workflow()
        assert len(wf.edges) == 5

    def test_study_node_is_fn(self) -> None:
        wf = workflow()
        node = wf.nodes["study"]
        assert isinstance(node, FnNode)
        assert "*.py" in node.command
        assert "task-instruction" in node.command
        assert "NotImplementedError" in node.command

    def test_builder_node(self) -> None:
        wf = workflow()
        node = wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.BUILDER
        assert node.max_iterations == 3
        assert node.timeout == 7200
        assert "interface" in node.prompt_template.lower()
        assert "nameerror" in node.prompt_template.lower()
        assert "cross-file" in node.prompt_template.lower()

    def test_gate_tests_is_fn_evaluator(self) -> None:
        """Gate uses fn evaluator (not agent) for speed and determinism."""
        wf = workflow()
        node = wf.nodes["gate_tests"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"
        assert node.evaluator_command is not None
        assert "pytest" in node.evaluator_command
        assert "gate-pytest-output.txt" in node.evaluator_command
        assert "pass:" in node.evaluator_command
        assert "reloop:" in node.evaluator_command
        assert "fail:" in node.evaluator_command

    def test_auto_merge_node(self) -> None:
        wf = workflow()
        node = wf.nodes["auto_merge"]
        assert isinstance(node, FnNode)
        assert "git update-ref" in node.command

    def test_proceed_edge_to_merge(self) -> None:
        """gate_tests has a PROCEED edge to auto_merge."""
        wf = workflow()
        proceed_edges = [
            e for e in wf.edges
            if e.source == "gate_tests"
            and e.target == "auto_merge"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed_edges) == 1

    def test_reloop_edge_exists(self) -> None:
        """gate_tests has a RELOOP edge to health_checker."""
        wf = workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_tests"
            and e.target == "health_checker"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1

    def test_health_checker_node(self) -> None:
        """health_checker is an AgentNode (NOT FnNode) with diagnostic prompt."""
        wf = workflow()
        node = wf.nodes["health_checker"]
        assert isinstance(node, AgentNode)
        assert not isinstance(node, FnNode)
        assert node.role == AgentRole.HEALTH_CHECKER
        assert "diagnostic" in node.prompt_template.lower()
        assert "do not run pytest" in node.prompt_template.lower()

    def test_health_checker_to_builder_edge(self) -> None:
        """health_checker has an unconditional edge to builder."""
        wf = workflow()
        edges = [
            e for e in wf.edges
            if e.source == "health_checker"
            and e.target == "builder"
            and e.condition is None
        ]
        assert len(edges) == 1

    def test_gate_saves_pytest_output(self) -> None:
        """gate_tests saves pytest stdout to gate-pytest-output.txt."""
        wf = workflow()
        node = wf.nodes["gate_tests"]
        assert isinstance(node, GateNode)
        assert node.evaluator_command is not None
        assert "gate-pytest-output.txt" in node.evaluator_command

    def test_gate_tracks_regression(self) -> None:
        """gate_tests tracks failure counts and detects regressions."""
        wf = workflow()
        node = wf.nodes["gate_tests"]
        assert isinstance(node, GateNode)
        assert node.evaluator_command is not None
        assert "gate-prev-fail-count.txt" in node.evaluator_command
        assert "fail:" in node.evaluator_command

    def test_builder_reads_health_check(self) -> None:
        """Builder prompt instructs reading health-check.md for RELOOP feedback."""
        wf = workflow()
        node = wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert "health-check.md" in node.prompt_template

    def test_no_eval_infrastructure(self) -> None:
        """No factory eval nodes (begin, finalize, precheck, study)."""
        wf = workflow()
        node_ids = set(wf.nodes.keys())
        assert "begin" not in node_ids
        assert "finalize" not in node_ids
        assert "gate_precheck" not in node_ids
        for node in wf.nodes.values():
            if isinstance(node, FnNode):
                assert "factory eval" not in node.command
                assert "factory finalize" not in node.command
                assert "factory precheck" not in node.command
                assert "factory begin" not in node.command


class TestFeaturebenchTerminal:
    """Tests for the terminal flag on featurebench workflow."""

    def test_workflow_is_terminal(self) -> None:
        wf = workflow()
        assert wf.terminal is True

    def test_registered_workflow_is_terminal(self) -> None:
        workflows = register_all()
        assert workflows["featurebench"].terminal is True


class TestFeaturebenchTrigger:
    """Tests for the trigger function."""

    def test_trigger_matches_featurebench_mode(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "featurebench"})

    def test_trigger_matches_without_factory(self) -> None:
        """Trigger fires on mode alone, regardless of project state."""
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "featurebench"})
        assert wf.trigger(ProjectState.NO_FACTORY, {"mode": "featurebench"})

    def test_trigger_rejects_other_modes(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "swebench"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


class TestFeaturebenchRegistration:
    """Tests for registration in the global workflow registry."""

    def test_registered_in_register_all(self) -> None:
        workflows = register_all()
        assert "featurebench" in workflows

    def test_registered_workflow_valid(self) -> None:
        workflows = register_all()
        wf = workflows["featurebench"]
        issues = wf.validate_graph()
        assert issues == [], f"Registered featurebench workflow has issues: {issues}"

    def test_registered_workflow_has_trigger(self) -> None:
        workflows = register_all()
        wf = workflows["featurebench"]
        assert wf.trigger is not None


class TestFeaturebenchMeta:
    """Tests for the module-level meta dict."""

    def test_meta_has_name(self) -> None:
        assert meta["name"] == "featurebench"

    def test_meta_has_description(self) -> None:
        assert "featurebench" in meta["description"].lower() or "FeatureBench" in meta["description"]
