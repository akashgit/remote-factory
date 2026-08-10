"""Tests for the AutomationBench-Sales contributed workflow."""

from __future__ import annotations

from factory.models import ProjectState
from factory.workflow.contributed.automationbench_sales import meta, workflow
from factory.workflow.definitions import register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    GateNode,
    VerdictType,
)


class TestAutomationbenchSalesWorkflow:
    """Tests for automationbench-sales workflow graph structure."""

    def test_workflow_name(self) -> None:
        wf = workflow()
        assert wf.name == "automationbench-sales"

    def test_node_count(self) -> None:
        """Workflow has exactly 10 nodes."""
        wf = workflow()
        assert len(wf.nodes) == 10
        assert set(wf.nodes.keys()) == {
            "research",
            "data_prep",
            "gate_data",
            "train",
            "gate_train",
            "serve",
            "gate_serve",
            "eval_bench",
            "verdict_gate",
            "archivist",
        }

    def test_start_node(self) -> None:
        wf = workflow()
        assert wf.start_node == "research"

    def test_graph_validates(self) -> None:
        """Graph passes structural validation (DAG check, edge consistency)."""
        wf = workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Workflow has validation issues: {issues}"

    def test_edge_count(self) -> None:
        """10 edges in the workflow (including RELOOP back-edge)."""
        wf = workflow()
        assert len(wf.edges) == 10

    def test_node_types(self) -> None:
        """Verify each node is the correct type."""
        wf = workflow()
        # AgentNodes
        assert isinstance(wf.nodes["research"], AgentNode)
        assert wf.nodes["research"].role == AgentRole.RESEARCHER
        assert isinstance(wf.nodes["data_prep"], AgentNode)
        assert wf.nodes["data_prep"].role == AgentRole.BUILDER
        assert isinstance(wf.nodes["train"], AgentNode)
        assert wf.nodes["train"].role == AgentRole.BUILDER
        assert wf.nodes["train"].timeout == 3600
        assert isinstance(wf.nodes["serve"], AgentNode)
        assert wf.nodes["serve"].role == AgentRole.BUILDER
        assert isinstance(wf.nodes["archivist"], AgentNode)
        assert wf.nodes["archivist"].role == AgentRole.ARCHIVIST
        assert wf.nodes["archivist"].blocking is False
        # GateNodes
        assert isinstance(wf.nodes["gate_data"], GateNode)
        assert wf.nodes["gate_data"].evaluator_type == "fn"
        assert isinstance(wf.nodes["gate_train"], GateNode)
        assert wf.nodes["gate_train"].evaluator_type == "fn"
        assert isinstance(wf.nodes["gate_serve"], GateNode)
        assert wf.nodes["gate_serve"].evaluator_type == "fn"
        assert isinstance(wf.nodes["verdict_gate"], GateNode)
        assert wf.nodes["verdict_gate"].evaluator_type == "fn"
        # FnNode
        assert isinstance(wf.nodes["eval_bench"], FnNode)

    def test_reloop_edge_exists(self) -> None:
        """verdict_gate has a RELOOP edge back to data_prep."""
        wf = workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "verdict_gate"
            and e.target == "data_prep"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1

    def test_proceed_edge_to_archivist(self) -> None:
        """verdict_gate has a PROCEED edge to archivist."""
        wf = workflow()
        proceed_edges = [
            e for e in wf.edges
            if e.source == "verdict_gate"
            and e.target == "archivist"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed_edges) == 1


class TestAutomationbenchSalesTerminal:
    """Tests for the terminal flag."""

    def test_workflow_is_terminal(self) -> None:
        wf = workflow()
        assert wf.terminal is True

    def test_registered_workflow_is_terminal(self) -> None:
        workflows = register_all()
        assert workflows["automationbench-sales"].terminal is True


class TestAutomationbenchSalesTrigger:
    """Tests for the trigger function."""

    def test_trigger_matches_mode(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "automationbench-sales"})

    def test_trigger_matches_without_factory(self) -> None:
        """Trigger fires on mode alone, regardless of project state."""
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "automationbench-sales"})
        assert wf.trigger(ProjectState.NO_FACTORY, {"mode": "automationbench-sales"})

    def test_trigger_rejects_other_modes(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "swebench"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


class TestAutomationbenchSalesRegistration:
    """Tests for registration in the global workflow registry."""

    def test_registered_in_register_all(self) -> None:
        workflows = register_all()
        assert "automationbench-sales" in workflows

    def test_registered_workflow_valid(self) -> None:
        workflows = register_all()
        wf = workflows["automationbench-sales"]
        issues = wf.validate_graph()
        assert issues == [], f"Registered workflow has issues: {issues}"

    def test_registered_workflow_has_trigger(self) -> None:
        workflows = register_all()
        wf = workflows["automationbench-sales"]
        assert wf.trigger is not None


class TestAutomationbenchSalesMeta:
    """Tests for the module-level meta dict."""

    def test_meta_has_name(self) -> None:
        assert meta["name"] == "automationbench-sales"

    def test_meta_has_description(self) -> None:
        desc = meta["description"].lower()
        assert "automationbench" in desc or "sales" in desc
