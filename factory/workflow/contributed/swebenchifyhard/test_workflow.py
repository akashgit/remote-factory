"""Tests for the SWE-benchify-hard contributed workflow."""

from __future__ import annotations

from factory.models import ProjectState
from factory.workflow.contributed.swebenchifyhard import meta, workflow
from factory.workflow.definitions import register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    GateNode,
    VerdictType,
)


class TestSwebenchifyHardWorkflow:
    """Tests for swebenchifyhard workflow graph structure."""

    def test_workflow_name(self) -> None:
        wf = workflow()
        assert wf.name == "swebenchifyhard"

    def test_node_count(self) -> None:
        wf = workflow()
        assert len(wf.nodes) == 4
        assert set(wf.nodes.keys()) == {"study", "builder", "gate_verify", "auto_merge"}

    def test_start_node(self) -> None:
        wf = workflow()
        assert wf.start_node == "study"

    def test_graph_validates(self) -> None:
        wf = workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Workflow has validation issues: {issues}"

    def test_edge_count(self) -> None:
        wf = workflow()
        assert len(wf.edges) == 4

    def test_study_node_is_fn(self) -> None:
        wf = workflow()
        node = wf.nodes["study"]
        assert isinstance(node, FnNode)
        assert "find" in node.command
        assert "task-instruction" in node.command

    def test_builder_node(self) -> None:
        wf = workflow()
        node = wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.BUILDER
        assert node.max_iterations == 3
        assert node.timeout == 7200
        assert "MINIMAL" in node.prompt_template
        assert "go test" in node.prompt_template.lower()

    def test_gate_verify_is_fn_evaluator(self) -> None:
        wf = workflow()
        node = wf.nodes["gate_verify"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"
        assert node.evaluator_command is not None
        assert "pass:" in node.evaluator_command
        assert "reloop:" in node.evaluator_command
        assert "fail:" in node.evaluator_command

    def test_auto_merge_node(self) -> None:
        wf = workflow()
        node = wf.nodes["auto_merge"]
        assert isinstance(node, FnNode)
        assert "git update-ref" in node.command

    def test_proceed_edge_to_merge(self) -> None:
        wf = workflow()
        proceed_edges = [
            e for e in wf.edges
            if e.source == "gate_verify"
            and e.target == "auto_merge"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed_edges) == 1

    def test_reloop_edge_exists(self) -> None:
        wf = workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_verify"
            and e.target == "builder"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1

    def test_no_eval_infrastructure(self) -> None:
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

    def test_no_deep_qa_nodes(self) -> None:
        wf = workflow()
        node_ids = set(wf.nodes.keys())
        assert "health_checker" not in node_ids
        assert "code_reviewer" not in node_ids
        assert "adversarial_tester" not in node_ids
        assert "gate_review" not in node_ids

    def test_no_research_strategy_nodes(self) -> None:
        wf = workflow()
        node_ids = set(wf.nodes.keys())
        assert "researcher" not in node_ids
        assert "strategist" not in node_ids
        assert "gate_research" not in node_ids
        assert "gate_strategy" not in node_ids


class TestSwebenchifyHardTrigger:

    def test_trigger_matches_mode(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "swebenchifyhard"})

    def test_trigger_matches_without_factory(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "swebenchifyhard"})
        assert wf.trigger(ProjectState.NO_FACTORY, {"mode": "swebenchifyhard"})

    def test_trigger_rejects_other_modes(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "swebench"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


class TestSwebenchifyHardRegistration:

    def test_registered_in_register_all(self) -> None:
        workflows = register_all()
        assert "swebenchifyhard" in workflows

    def test_registered_workflow_valid(self) -> None:
        workflows = register_all()
        wf = workflows["swebenchifyhard"]
        issues = wf.validate_graph()
        assert issues == [], f"Registered workflow has issues: {issues}"

    def test_registered_workflow_has_trigger(self) -> None:
        workflows = register_all()
        wf = workflows["swebenchifyhard"]
        assert wf.trigger is not None


class TestSwebenchifyHardMeta:

    def test_meta_has_name(self) -> None:
        assert meta["name"] == "swebenchifyhard"

    def test_meta_has_description(self) -> None:
        assert "benchify" in meta["description"].lower()
