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
        """Workflow has exactly 6 nodes for the hybrid host/container pipeline."""
        wf = workflow()
        assert len(wf.nodes) == 6
        assert set(wf.nodes.keys()) == {
            "researcher",
            "strategist",
            "builder",
            "adversarial_tester",
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
        """6 edges for the single-loop architecture."""
        wf = workflow()
        assert len(wf.edges) == 6

    def test_researcher_node(self) -> None:
        wf = workflow()
        node = wf.nodes["researcher"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.RESEARCHER
        assert node.metadata.get("execution_context") is None

    def test_strategist_node(self) -> None:
        wf = workflow()
        node = wf.nodes["strategist"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST
        assert node.metadata.get("execution_context") is None

    def test_builder_node(self) -> None:
        wf = workflow()
        node = wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.BUILDER
        assert node.max_iterations == 3
        assert node.timeout == 1200

    def test_adversarial_tester_node(self) -> None:
        wf = workflow()
        node = wf.nodes["adversarial_tester"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.ADVERSARIAL_TESTER
        assert node.timeout == 1800
        assert ".factory/reviews/adversarial-qa.md" in node.writes
        assert "validation_tests" in node.prompt_template
        assert "problem_statement" in node.prompt_template

    def test_gate_tests_is_fn_evaluator(self) -> None:
        wf = workflow()
        node = wf.nodes["gate_tests"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"
        assert node.evaluator_command is not None
        assert "docker exec" in node.evaluator_command
        assert "gate-pytest-output.txt" in node.evaluator_command
        assert "pass:" in node.evaluator_command
        assert "reloop:" in node.evaluator_command

    def test_archivist_non_blocking(self) -> None:
        wf = workflow()
        node = wf.nodes["archivist"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.ARCHIVIST
        assert node.blocking is False
        assert node.model == "haiku"
        assert node.metadata.get("execution_context") is None

    def test_reloop_edge(self) -> None:
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

    def test_host_nodes_no_container_metadata(self) -> None:
        """Adversarial tester, gate, and archivist do not need container context."""
        wf = workflow()
        for nid in ("adversarial_tester", "gate_tests", "archivist"):
            assert wf.nodes[nid].metadata.get("execution_context") != "container"

    def test_adversarial_tester_writes_tests(self) -> None:
        """Adversarial tester writes tests but does NOT run them."""
        wf = workflow()
        node = wf.nodes["adversarial_tester"]
        assert "Write" in node.prompt_template or "write" in node.prompt_template.lower()
        assert "Do NOT run pytest" in node.prompt_template

    def test_gate_tests_uses_docker_exec(self) -> None:
        """Gate runs validation tests inside the container via docker exec."""
        wf = workflow()
        node = wf.nodes["gate_tests"]
        assert "docker exec" in node.evaluator_command

    def test_builder_to_adversarial_tester_edge(self) -> None:
        """Unconditional edge from builder to adversarial_tester."""
        wf = workflow()
        edges = [
            e for e in wf.edges
            if e.source == "builder"
            and e.target == "adversarial_tester"
            and e.condition is None
        ]
        assert len(edges) == 1

    def test_adversarial_tester_to_gate_tests_edge(self) -> None:
        """Unconditional edge from adversarial_tester to gate_tests."""
        wf = workflow()
        edges = [
            e for e in wf.edges
            if e.source == "adversarial_tester"
            and e.target == "gate_tests"
            and e.condition is None
        ]
        assert len(edges) == 1


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

    def test_meta_mentions_hybrid(self) -> None:
        assert "hybrid" in meta["description"].lower()
