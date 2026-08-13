"""Tests for the Lumen contributed workflow."""

from __future__ import annotations

from factory.models import ProjectState
from factory.workflow.contributed.lumen import meta, workflow
from factory.workflow.definitions import register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    GateNode,
    VerdictType,
)


class TestLumenWorkflow:
    """Tests for lumen workflow graph structure."""

    def test_workflow_name(self) -> None:
        wf = workflow()
        assert wf.name == "lumen"

    def test_node_count(self) -> None:
        """Workflow has exactly 5 nodes: setup, config_gate, lumen_context_agent, rl_train, check_gate."""
        wf = workflow()
        assert len(wf.nodes) == 5
        assert set(wf.nodes.keys()) == {
            "setup", "config_gate", "lumen_context_agent", "rl_train", "check_gate",
        }

    def test_start_node(self) -> None:
        wf = workflow()
        assert wf.start_node == "setup"

    def test_graph_validates(self) -> None:
        """Graph passes structural validation (DAG check, edge consistency)."""
        wf = workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Workflow has validation issues: {issues}"

    def test_edge_count(self) -> None:
        """5 edges: setup->config_gate, config_gate->lumen, lumen->train, train->gate, gate->lumen RELOOP."""
        wf = workflow()
        assert len(wf.edges) == 5

    def test_setup_node_is_fn(self) -> None:
        wf = workflow()
        node = wf.nodes["setup"]
        assert isinstance(node, FnNode)
        assert "factory.lumen.preflight" in node.command

    def test_config_gate_node(self) -> None:
        wf = workflow()
        node = wf.nodes["config_gate"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "user"

    def test_lumen_context_agent_node(self) -> None:
        wf = workflow()
        node = wf.nodes["lumen_context_agent"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.LUMEN_CONTEXT_AGENT
        assert "prompts.json" in str(node.writes)

    def test_rl_train_node_is_fn(self) -> None:
        wf = workflow()
        node = wf.nodes["rl_train"]
        assert isinstance(node, FnNode)
        assert "factory.lumen.train" in node.command

    def test_check_gate_node(self) -> None:
        wf = workflow()
        node = wf.nodes["check_gate"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"
        assert node.evaluator_command is not None
        assert "pass:" in node.evaluator_command
        assert "reloop:" in node.evaluator_command
        assert "halt:" in node.evaluator_command

    def test_reloop_edge_exists(self) -> None:
        """check_gate has a RELOOP edge back to lumen_context_agent."""
        wf = workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "check_gate"
            and e.target == "lumen_context_agent"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1


class TestLumenTerminal:
    """Tests for terminal workflow property."""

    def test_workflow_is_terminal(self) -> None:
        """Lumen is a terminal workflow (doesn't chain to other modes)."""
        wf = workflow()
        assert wf.terminal is True

    def test_registered_workflow_is_terminal(self) -> None:
        """Registered workflow also has terminal=True."""
        workflows = register_all()
        lumen_wf = workflows.get("lumen")
        assert lumen_wf is not None, "lumen not registered"
        assert lumen_wf.terminal is True


class TestLumenTrigger:
    """Tests for the trigger function."""

    def test_trigger_matches_lumen_mode(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "lumen"})

    def test_trigger_matches_without_factory(self) -> None:
        """Trigger fires on mode alone, regardless of project state."""
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "lumen"})
        assert wf.trigger(ProjectState.NO_FACTORY, {"mode": "lumen"})

    def test_trigger_rejects_other_modes(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "build"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


class TestLumenRegistration:
    """Tests for workflow registration."""

    def test_registered_in_register_all(self) -> None:
        """Workflow appears in register_all() output."""
        workflows = register_all()
        assert "lumen" in workflows

    def test_registered_workflow_valid(self) -> None:
        """Registered workflow passes validation."""
        workflows = register_all()
        wf = workflows["lumen"]
        issues = wf.validate_graph()
        assert issues == [], f"Registered lumen workflow has issues: {issues}"

    def test_registered_workflow_has_trigger(self) -> None:
        workflows = register_all()
        wf = workflows["lumen"]
        assert wf.trigger is not None


class TestLumenMeta:
    """Tests for workflow metadata."""

    def test_meta_has_name(self) -> None:
        assert "name" in meta
        assert meta["name"] == "lumen"

    def test_meta_has_description(self) -> None:
        assert "description" in meta
        assert len(meta["description"]) > 0
        assert "lumen" in meta["description"].lower()
