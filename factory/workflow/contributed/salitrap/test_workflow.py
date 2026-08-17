"""Tests for the SaliTrap contributed workflow."""

from __future__ import annotations

from factory.models import ProjectState
from factory.workflow.contributed.salitrap import meta, workflow
from factory.workflow.definitions import register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    GateNode,
    VerdictType,
)


class TestSalitrapWorkflow:
    """Tests for salitrap workflow graph structure."""

    def test_workflow_name(self) -> None:
        wf = workflow()
        assert wf.name == "salitrap"

    def test_node_count(self) -> None:
        """Workflow has exactly 4 nodes: study, solver, gate_verify, auto_merge."""
        wf = workflow()
        assert len(wf.nodes) == 4
        assert set(wf.nodes.keys()) == {"study", "solver", "gate_verify", "auto_merge"}

    def test_start_node(self) -> None:
        wf = workflow()
        assert wf.start_node == "study"

    def test_graph_validates(self) -> None:
        """Graph passes structural validation (DAG check, edge consistency)."""
        wf = workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Workflow has validation issues: {issues}"

    def test_edge_count(self) -> None:
        """4 edges: study->solver, solver->gate, gate->merge, gate->solver RELOOP."""
        wf = workflow()
        assert len(wf.edges) == 4

    def test_study_node_is_fn(self) -> None:
        wf = workflow()
        node = wf.nodes["study"]
        assert isinstance(node, FnNode)
        assert "task-instruction" in node.command

    def test_solver_node(self) -> None:
        wf = workflow()
        node = wf.nodes["solver"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.BUILDER
        assert node.model == "opus"
        assert node.max_iterations == 3
        assert node.timeout == 3600

    def test_solver_has_physics_aware_priming(self) -> None:
        """Solver prompt includes physics-aware priming per SaliTrap paper P1 intervention."""
        wf = workflow()
        node = wf.nodes["solver"]
        assert isinstance(node, AgentNode)
        assert "prerequisite" in node.prompt_template.lower()
        assert "physical" in node.prompt_template.lower()
        assert "infeasible" in node.prompt_template.lower()
        assert "trap" in node.prompt_template.lower()

    def test_solver_checks_four_trap_dimensions(self) -> None:
        """Solver prompt references all 4 SaliTrap trap dimensions."""
        wf = workflow()
        node = wf.nodes["solver"]
        assert isinstance(node, AgentNode)
        assert "Missing Prerequisite" in node.prompt_template
        assert "Environmental Mismatch" in node.prompt_template
        assert "Temporal/Physiological" in node.prompt_template
        assert "Rule Mismatch" in node.prompt_template

    def test_solver_writes_answer_file(self) -> None:
        """Solver writes structured answer to /workspace/answer.txt."""
        wf = workflow()
        node = wf.nodes["solver"]
        assert isinstance(node, AgentNode)
        assert "answer.txt" in node.prompt_template
        assert "/workspace/answer.txt" in node.writes

    def test_gate_verify_is_fn_evaluator(self) -> None:
        """Gate uses fn evaluator (not agent) for speed and determinism."""
        wf = workflow()
        node = wf.nodes["gate_verify"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"
        assert node.evaluator_command is not None
        assert "pass:" in node.evaluator_command
        assert "reloop:" in node.evaluator_command

    def test_gate_verify_checks_answer_file(self) -> None:
        """Gate checks /workspace/answer.txt exists and has content."""
        wf = workflow()
        node = wf.nodes["gate_verify"]
        assert isinstance(node, GateNode)
        assert node.evaluator_command is not None
        assert "answer.txt" in node.evaluator_command

    def test_auto_merge_node(self) -> None:
        wf = workflow()
        node = wf.nodes["auto_merge"]
        assert isinstance(node, FnNode)
        assert "git update-ref" in node.command

    def test_proceed_edge_to_merge(self) -> None:
        """gate_verify has a PROCEED edge to auto_merge."""
        wf = workflow()
        proceed_edges = [
            e for e in wf.edges
            if e.source == "gate_verify"
            and e.target == "auto_merge"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed_edges) == 1

    def test_reloop_edge_exists(self) -> None:
        """gate_verify has a RELOOP edge back to solver."""
        wf = workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_verify"
            and e.target == "solver"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1

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

    def test_no_deep_qa_nodes(self) -> None:
        """No deep-QA pipeline nodes."""
        wf = workflow()
        node_ids = set(wf.nodes.keys())
        assert "health_checker" not in node_ids
        assert "code_reviewer" not in node_ids
        assert "adversarial_tester" not in node_ids
        assert "gate_review" not in node_ids

    def test_no_research_strategy_nodes(self) -> None:
        """No researcher or strategist nodes."""
        wf = workflow()
        node_ids = set(wf.nodes.keys())
        assert "researcher" not in node_ids
        assert "strategist" not in node_ids
        assert "gate_research" not in node_ids
        assert "gate_strategy" not in node_ids


class TestSalitrapTerminal:
    """Tests for the terminal flag on salitrap workflow."""

    def test_workflow_is_terminal(self) -> None:
        wf = workflow()
        assert wf.terminal is True

    def test_registered_workflow_is_terminal(self) -> None:
        workflows = register_all()
        assert workflows["salitrap"].terminal is True


class TestSalitrapTrigger:
    """Tests for the trigger function."""

    def test_trigger_matches_salitrap_mode(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "salitrap"})

    def test_trigger_matches_without_factory(self) -> None:
        """Trigger fires on mode alone, regardless of project state."""
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "salitrap"})
        assert wf.trigger(ProjectState.NO_FACTORY, {"mode": "salitrap"})

    def test_trigger_rejects_other_modes(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "swebench"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


class TestSalitrapRegistration:
    """Tests for registration in the global workflow registry."""

    def test_registered_in_register_all(self) -> None:
        workflows = register_all()
        assert "salitrap" in workflows

    def test_registered_workflow_valid(self) -> None:
        workflows = register_all()
        wf = workflows["salitrap"]
        issues = wf.validate_graph()
        assert issues == [], f"Registered salitrap workflow has issues: {issues}"

    def test_registered_workflow_has_trigger(self) -> None:
        workflows = register_all()
        wf = workflows["salitrap"]
        assert wf.trigger is not None


class TestSalitrapMeta:
    """Tests for the module-level meta dict."""

    def test_meta_has_name(self) -> None:
        assert meta["name"] == "salitrap"

    def test_meta_has_description(self) -> None:
        assert "salitrap" in meta["description"].lower() or "SaliTrap" in meta["description"]
