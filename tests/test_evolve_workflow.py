"""Tests for the evolve workflow definition."""

from factory.workflow.definitions import evolve_workflow, register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    GateNode,
    VerdictType,
)
from factory.models import ProjectState


class TestEvolveWorkflowStructure:
    """Test the evolve workflow graph structure."""

    def test_workflow_creation(self):
        """evolve_workflow() returns a valid Workflow with name='evolve'."""
        wf = evolve_workflow()
        assert wf.name == "evolve"
        assert wf.start_node == "baseline"

    def test_graph_validates(self):
        """Workflow graph passes structural validation (no dangling edges, unreachable nodes)."""
        wf = evolve_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Validation issues: {issues}"

    def test_required_nodes_present(self):
        """All expected nodes exist in the workflow."""
        wf = evolve_workflow()
        expected_nodes = {
            "baseline", "researcher", "gate_research",
            "strategist", "gate_strategy", "begin", "builder",
            "gate_build", "health_checker", "gate_eval",
            "finalize", "archivist", "gate_convergence",
            "archivist_final",
        }
        assert expected_nodes.issubset(set(wf.nodes.keys()))

    def test_node_types(self):
        """Verify each node has the correct type."""
        wf = evolve_workflow()
        assert isinstance(wf.nodes["baseline"], FnNode)
        assert isinstance(wf.nodes["researcher"], AgentNode)
        assert isinstance(wf.nodes["gate_research"], GateNode)
        assert isinstance(wf.nodes["strategist"], AgentNode)
        assert isinstance(wf.nodes["gate_strategy"], GateNode)
        assert isinstance(wf.nodes["begin"], FnNode)
        assert isinstance(wf.nodes["builder"], AgentNode)
        assert isinstance(wf.nodes["gate_build"], GateNode)
        assert isinstance(wf.nodes["health_checker"], AgentNode)
        assert isinstance(wf.nodes["gate_eval"], GateNode)
        assert isinstance(wf.nodes["finalize"], FnNode)
        assert isinstance(wf.nodes["archivist"], AgentNode)
        assert isinstance(wf.nodes["gate_convergence"], GateNode)
        assert isinstance(wf.nodes["archivist_final"], AgentNode)

    def test_agent_roles(self):
        """Verify each AgentNode has the correct role."""
        wf = evolve_workflow()
        assert wf.nodes["researcher"].role == AgentRole.RESEARCHER
        assert wf.nodes["strategist"].role == AgentRole.STRATEGIST
        assert wf.nodes["builder"].role == AgentRole.BUILDER
        assert wf.nodes["health_checker"].role == AgentRole.HEALTH_CHECKER
        assert wf.nodes["archivist"].role == AgentRole.ARCHIVIST
        assert wf.nodes["archivist_final"].role == AgentRole.ARCHIVIST

    def test_archivist_non_blocking(self):
        """The mid-loop archivist is non-blocking (fire-and-forget)."""
        wf = evolve_workflow()
        assert wf.nodes["archivist"].blocking is False

    def test_archivist_final_blocking(self):
        """The final archivist is blocking (must complete before exit)."""
        wf = evolve_workflow()
        assert wf.nodes["archivist_final"].blocking is True

    def test_builder_timeout(self):
        """Builder has an extended timeout for code modification work."""
        wf = evolve_workflow()
        assert wf.nodes["builder"].timeout == 1200


class TestEvolveWorkflowEdges:
    """Test edge wiring and conditional routing."""

    def test_evolution_loop_exists(self):
        """There is a RELOOP edge from gate_convergence back to strategist."""
        wf = evolve_workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_convergence"
            and e.target == "strategist"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1, "Missing convergence->strategist RELOOP edge"

    def test_convergence_proceed_to_final(self):
        """PROCEED from gate_convergence leads to archivist_final."""
        wf = evolve_workflow()
        proceed_edges = [
            e for e in wf.edges
            if e.source == "gate_convergence"
            and e.target == "archivist_final"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed_edges) == 1

    def test_build_gate_reloop_to_builder(self):
        """gate_build RELOOP goes back to builder."""
        wf = evolve_workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_build"
            and e.target == "builder"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1

    def test_strategy_gate_reloop_to_strategist(self):
        """gate_strategy RELOOP goes back to strategist."""
        wf = evolve_workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_strategy"
            and e.target == "strategist"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1

    def test_research_gate_reloop_to_researcher(self):
        """gate_research RELOOP goes back to researcher."""
        wf = evolve_workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_research"
            and e.target == "researcher"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1

    def test_eval_gate_proceeds_to_finalize(self):
        """gate_eval PROCEED goes to finalize."""
        wf = evolve_workflow()
        proceed_edges = [
            e for e in wf.edges
            if e.source == "gate_eval"
            and e.target == "finalize"
            and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed_edges) == 1


class TestEvolveWorkflowTrigger:
    """Test the trigger function."""

    def test_trigger_on_evolve_mode(self):
        """Trigger fires when ctx.mode == 'evolve'."""
        wf = evolve_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "evolve"}) is True

    def test_trigger_false_for_other_modes(self):
        """Trigger does not fire for non-evolve modes."""
        wf = evolve_workflow()
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"}) is False
        assert wf.trigger(ProjectState.HAS_FACTORY, {}) is False

    def test_trigger_independent_of_state(self):
        """Trigger fires regardless of project state when mode is evolve."""
        wf = evolve_workflow()
        for state in ProjectState:
            assert wf.trigger(state, {"mode": "evolve"}) is True


class TestEvolveWorkflowRegistration:
    """Test workflow registration."""

    def test_registered_in_register_all(self):
        """evolve_workflow is included in register_all() output."""
        workflows = register_all()
        assert "evolve" in workflows
        assert workflows["evolve"].name == "evolve"


class TestEvolveWorkflowSkillExport:
    """Test SKILL.md generation."""

    def test_skill_export_succeeds(self):
        """workflow_to_skill_md produces valid output for evolve workflow."""
        from factory.workflow.skill_export import workflow_to_skill_md, validate_skill
        wf = evolve_workflow()
        skill_md = workflow_to_skill_md(wf)
        issues = validate_skill(skill_md)
        assert issues == [], f"Skill validation issues: {issues}"

    def test_skill_has_frontmatter(self):
        """Generated SKILL.md includes proper frontmatter."""
        from factory.workflow.skill_export import workflow_to_skill_md
        wf = evolve_workflow()
        skill_md = workflow_to_skill_md(wf)
        assert skill_md.startswith("---")
        assert "workflow-evolve" in skill_md
