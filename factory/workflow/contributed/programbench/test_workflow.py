"""Tests for the ProgramBench contributed workflow."""

from __future__ import annotations

from factory.models import ProjectState
from factory.workflow.contributed.programbench import meta, workflow
from factory.workflow.definitions import register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    GateNode,
    VerdictType,
)


class TestProgrambenchWorkflow:
    """Tests for programbench workflow graph structure."""

    def test_workflow_name(self) -> None:
        wf = workflow()
        assert wf.name == "programbench"

    def test_node_count(self) -> None:
        """Workflow has exactly 4 nodes: builder, reviewer, gate_verify, auto_merge."""
        wf = workflow()
        assert len(wf.nodes) == 4
        assert set(wf.nodes.keys()) == {"builder", "reviewer", "gate_verify", "auto_merge"}

    def test_start_node(self) -> None:
        wf = workflow()
        assert wf.start_node == "builder"

    def test_graph_validates(self) -> None:
        """Graph passes structural validation (DAG check, edge consistency)."""
        wf = workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Workflow has validation issues: {issues}"

    def test_edge_count(self) -> None:
        """4 edges: builder->reviewer, reviewer->gate, gate->merge, gate->builder RELOOP."""
        wf = workflow()
        assert len(wf.edges) == 4

    def test_builder_node(self) -> None:
        wf = workflow()
        node = wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.BUILDER
        assert node.max_iterations == 3
        assert node.timeout == 1200
        assert "discoveries.md" in node.prompt_template
        assert "autonomous" in node.prompt_template.lower()
        assert "__DATE__" in node.prompt_template

    def test_builder_maintains_discoveries(self) -> None:
        """Builder prompt instructs maintaining a structured discoveries file."""
        wf = workflow()
        node = wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert "discoveries.md" in node.prompt_template
        assert "## Discovery:" in node.prompt_template
        assert "verified" in node.prompt_template
        assert "uncertain" in node.prompt_template
        assert "unexplored" in node.prompt_template
        assert "Evidence" in node.prompt_template

    def test_builder_reads_todos(self) -> None:
        """Builder prompt instructs reading todos.md on RELOOP iterations."""
        wf = workflow()
        node = wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert "todos.md" in node.prompt_template
        assert "address EACH item" in node.prompt_template

    def test_builder_backs_up_binary(self) -> None:
        """Builder prompt instructs backing up executable to executable.bak."""
        wf = workflow()
        node = wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert "executable.bak" in node.prompt_template
        assert "cp /workspace/executable /workspace/executable.bak" in node.prompt_template

    def test_reviewer_node(self) -> None:
        wf = workflow()
        node = wf.nodes["reviewer"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.RESEARCHER
        assert "adversarial" in node.prompt_template.lower()
        assert "autonomous" in node.prompt_template.lower()
        assert "discoveries.md" in node.prompt_template

    def test_reviewer_validates_discoveries(self) -> None:
        """Reviewer compares builder output against ground truth binary."""
        wf = workflow()
        node = wf.nodes["reviewer"]
        assert isinstance(node, AgentNode)
        assert "executable.bak" in node.prompt_template
        assert "executable" in node.prompt_template
        assert "verified" in node.prompt_template
        assert "incorrect" in node.prompt_template
        assert "unexplored" in node.prompt_template

    def test_reviewer_writes_todos(self) -> None:
        """Reviewer writes todos.md with specific tasks for the builder."""
        wf = workflow()
        node = wf.nodes["reviewer"]
        assert isinstance(node, AgentNode)
        assert "todos.md" in node.prompt_template
        assert "## TODO:" in node.prompt_template
        assert "Expected" in node.prompt_template
        assert "Actual" in node.prompt_template
        assert "Action" in node.prompt_template

    def test_reviewer_probes_unknown_unknowns(self) -> None:
        """Reviewer probes for behaviors the builder didn't think of."""
        wf = workflow()
        node = wf.nodes["reviewer"]
        assert isinstance(node, AgentNode)
        assert "unknown" in node.prompt_template.lower()
        assert "ADDITIONAL" in node.prompt_template

    def test_gate_verify_is_fn_evaluator(self) -> None:
        """Gate uses fn evaluator (not agent) for speed and determinism."""
        wf = workflow()
        node = wf.nodes["gate_verify"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"
        assert node.evaluator_command is not None
        assert "pass:" in node.evaluator_command
        assert "reloop:" in node.evaluator_command

    def test_gate_verify_checks_todos(self) -> None:
        """Gate checks todos.md for remaining items."""
        wf = workflow()
        node = wf.nodes["gate_verify"]
        assert isinstance(node, GateNode)
        assert node.evaluator_command is not None
        assert "todos.md" in node.evaluator_command
        assert "## TODO" in node.evaluator_command

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
        """gate_verify has a RELOOP edge back to builder."""
        wf = workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_verify"
            and e.target == "builder"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1

    def test_builder_to_reviewer_edge(self) -> None:
        """builder feeds into reviewer."""
        wf = workflow()
        edges = [
            e for e in wf.edges
            if e.source == "builder" and e.target == "reviewer"
        ]
        assert len(edges) == 1

    def test_reviewer_to_gate_edge(self) -> None:
        """reviewer feeds into gate_verify."""
        wf = workflow()
        edges = [
            e for e in wf.edges
            if e.source == "reviewer" and e.target == "gate_verify"
        ]
        assert len(edges) == 1

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

    def test_no_discover_node(self) -> None:
        """Discover node was removed — builder does its own discovery."""
        wf = workflow()
        assert "discover" not in wf.nodes


class TestProgrambenchTerminal:
    """Tests for the terminal flag on programbench workflow."""

    def test_workflow_is_terminal(self) -> None:
        wf = workflow()
        assert wf.terminal is True

    def test_registered_workflow_is_terminal(self) -> None:
        workflows = register_all()
        assert workflows["programbench"].terminal is True


class TestProgrambenchTrigger:
    """Tests for the trigger function."""

    def test_trigger_matches_programbench_mode(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "programbench"})

    def test_trigger_matches_without_factory(self) -> None:
        """Trigger fires on mode alone, regardless of project state."""
        wf = workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "programbench"})
        assert wf.trigger(ProjectState.NO_FACTORY, {"mode": "programbench"})

    def test_trigger_rejects_other_modes(self) -> None:
        wf = workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "terminalbench"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


class TestProgrambenchRegistration:
    """Tests for registration in the global workflow registry."""

    def test_registered_in_register_all(self) -> None:
        workflows = register_all()
        assert "programbench" in workflows

    def test_registered_workflow_valid(self) -> None:
        workflows = register_all()
        wf = workflows["programbench"]
        issues = wf.validate_graph()
        assert issues == [], f"Registered programbench workflow has issues: {issues}"

    def test_registered_workflow_has_trigger(self) -> None:
        workflows = register_all()
        wf = workflows["programbench"]
        assert wf.trigger is not None


class TestProgrambenchMeta:
    """Tests for the module-level meta dict."""

    def test_meta_has_name(self) -> None:
        assert meta["name"] == "programbench"

    def test_meta_has_description(self) -> None:
        assert "programbench" in meta["description"].lower() or "ProgramBench" in meta["description"]
