"""Tests for the FeatureBench contributed workflow."""

from __future__ import annotations

from factory.models import ProjectState
from factory.workflow.contributed.featurebench import meta, workflow
from factory.workflow.definitions import register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    GateNode,
    VerdictType,
)


class TestFeaturebenchWorkflow:
    """Tests for featurebench workflow graph structure."""

    def test_workflow_name(self) -> None:
        wf = workflow()
        assert wf.name == "featurebench"

    def test_node_count(self) -> None:
        """Workflow has exactly 10 nodes for the two-loop QA pipeline."""
        wf = workflow()
        assert len(wf.nodes) == 10
        assert set(wf.nodes.keys()) == {
            "researcher",
            "strategist",
            "builder",
            "code_reviewer",
            "gate_review",
            "adversarial_tester",
            "gate_qa",
            "health_checker",
            "gate_tests",
            "archivist",
        }

    def test_start_node(self) -> None:
        wf = workflow()
        assert wf.start_node == "researcher"

    def test_graph_validates(self) -> None:
        """Graph passes structural validation (DAG check, edge consistency)."""
        wf = workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Workflow has validation issues: {issues}"

    def test_edge_count(self) -> None:
        """12 edges for the two-loop architecture."""
        wf = workflow()
        assert len(wf.edges) == 12

    def test_researcher_node(self) -> None:
        wf = workflow()
        node = wf.nodes["researcher"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.RESEARCHER

    def test_builder_node(self) -> None:
        wf = workflow()
        node = wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.BUILDER
        assert node.max_iterations == 3
        assert node.timeout == 1200

    def test_code_reviewer_node(self) -> None:
        wf = workflow()
        node = wf.nodes["code_reviewer"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.CODE_REVIEWER
        assert node.timeout == 900

    def test_gate_review_is_fn_evaluator(self) -> None:
        wf = workflow()
        node = wf.nodes["gate_review"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"
        assert node.evaluator_command is not None
        assert "CRITICAL_FOUND" in node.evaluator_command

    def test_adversarial_tester_node(self) -> None:
        wf = workflow()
        node = wf.nodes["adversarial_tester"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.ADVERSARIAL_TESTER
        assert node.timeout == 1800

    def test_gate_qa_is_agent_evaluator(self) -> None:
        wf = workflow()
        node = wf.nodes["gate_qa"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "agent"
        assert node.evaluator_role == AgentRole.CEO

    def test_gate_tests_is_fn_evaluator(self) -> None:
        wf = workflow()
        node = wf.nodes["gate_tests"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"
        assert node.evaluator_command is not None
        assert "RESOLVED" in node.evaluator_command

    def test_archivist_non_blocking(self) -> None:
        wf = workflow()
        node = wf.nodes["archivist"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.ARCHIVIST
        assert node.blocking is False
        assert node.model == "haiku"

    def test_proceed_edge_gate_review(self) -> None:
        wf = workflow()
        proceed_edges = [
            e for e in wf.edges
            if e.source == "gate_review"
            and e.target == "adversarial_tester"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed_edges) == 1

    def test_halt_edge_gate_review(self) -> None:
        wf = workflow()
        halt_edges = [
            e for e in wf.edges
            if e.source == "gate_review"
            and e.target == "health_checker"
            and e.condition == VerdictType.HALT
        ]
        assert len(halt_edges) == 1

    def test_qa_reloop_edge(self) -> None:
        """gate_qa has a RELOOP edge back to builder."""
        wf = workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_qa"
            and e.target == "builder"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1

    def test_test_reloop_edge(self) -> None:
        """gate_tests has a RELOOP edge back to builder."""
        wf = workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_tests"
            and e.target == "builder"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1

    def test_proceed_edge_to_archivist(self) -> None:
        wf = workflow()
        proceed_edges = [
            e for e in wf.edges
            if e.source == "gate_tests"
            and e.target == "archivist"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed_edges) == 1

    def test_no_eval_infrastructure(self) -> None:
        """No factory experiment tracking nodes."""
        wf = workflow()
        node_ids = set(wf.nodes.keys())
        assert "begin" not in node_ids
        assert "finalize" not in node_ids
        assert "gate_precheck" not in node_ids

    def test_no_user_gates(self) -> None:
        """Workflow is fully autonomous — no user approval gates."""
        wf = workflow()
        for node in wf.nodes.values():
            if isinstance(node, GateNode):
                assert node.evaluator_type != "user"


class TestFeaturebenchTerminal:
    def test_workflow_is_terminal(self) -> None:
        wf = workflow()
        assert wf.terminal is True

    def test_registered_workflow_is_terminal(self) -> None:
        workflows = register_all()
        assert workflows["featurebench"].terminal is True


class TestFeaturebenchTrigger:
    def test_trigger_matches_featurebench_mode(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "featurebench"})

    def test_trigger_matches_without_factory(self) -> None:
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
    def test_meta_has_name(self) -> None:
        assert meta["name"] == "featurebench"

    def test_meta_has_description(self) -> None:
        assert "FeatureBench" in meta["description"]

    def test_meta_mentions_two_loop(self) -> None:
        assert "two-loop" in meta["description"]
