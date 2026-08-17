"""Tests for the deep-research workflow (W₁₅ v5).

Validates graph structure, node properties, edge wiring, trigger function,
registration, and skill export. Verifies existing workflows are unchanged.

v5: Decomposer + researcher pipeline. Decomposer generates research directions;
researcher executes them. No ForkNode, no JoinNode, no parallel researchers.
"""

from __future__ import annotations

from factory.models import ProjectState
from factory.workflow.deep_research import workflow as deep_research_workflow
from factory.workflow.definitions import (
    _get_builtin_registry,
    register_all,
)
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    GateNode,
    Study,
    VerdictType,
)


# ── Graph structure ────────────────────────────────────────────────


class TestDeepResearchWorkflowStructure:
    def test_valid_graph(self) -> None:
        wf = deep_research_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"deep-research workflow has issues: {issues}"

    def test_name(self) -> None:
        wf = deep_research_workflow()
        assert wf.name == "deep-research"

    def test_start_node(self) -> None:
        wf = deep_research_workflow()
        assert wf.start_node == "study"

    def test_terminal(self) -> None:
        wf = deep_research_workflow()
        assert wf.terminal is True

    def test_has_expected_nodes(self) -> None:
        wf = deep_research_workflow()
        expected_nodes = {
            "study",
            "decomposer",
            "deep_researcher",
            "gate_coverage",
        }
        assert set(wf.nodes.keys()) == expected_nodes

    def test_node_count(self) -> None:
        wf = deep_research_workflow()
        assert len(wf.nodes) == 4

    def test_no_fork_or_join_nodes(self) -> None:
        """v4 constraint: no ForkNode or JoinNode in the graph."""
        from factory.workflow.primitives import ForkNode, JoinNode

        wf = deep_research_workflow()
        for nid, node in wf.nodes.items():
            assert not isinstance(node, ForkNode), f"unexpected ForkNode: {nid}"
            assert not isinstance(node, JoinNode), f"unexpected JoinNode: {nid}"


# ── Node types ────────────────────────────────────────────────────


class TestDeepResearchNodeTypes:
    def test_study_node_type(self) -> None:
        wf = deep_research_workflow()
        assert isinstance(wf.nodes["study"], Study)

    def test_decomposer_is_agent_node(self) -> None:
        wf = deep_research_workflow()
        node = wf.nodes["decomposer"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.RESEARCHER

    def test_decomposer_model_and_timeout(self) -> None:
        wf = deep_research_workflow()
        node = wf.nodes["decomposer"]
        assert isinstance(node, AgentNode)
        assert node.model == "sonnet"
        assert node.timeout == 120

    def test_decomposer_reads_observations(self) -> None:
        wf = deep_research_workflow()
        node = wf.nodes["decomposer"]
        assert ".factory/strategy/observations.md" in node.reads

    def test_decomposer_writes_directions(self) -> None:
        wf = deep_research_workflow()
        node = wf.nodes["decomposer"]
        assert ".factory/strategy/research-directions.md" in node.writes

    def test_decomposer_has_post_check(self) -> None:
        wf = deep_research_workflow()
        node = wf.nodes["decomposer"]
        assert isinstance(node, AgentNode)
        assert len(node.post_checks) == 1
        assert node.post_checks[0].must_exist is True
        assert node.post_checks[0].min_size == 200
        assert node.post_checks[0].path == ".factory/strategy/research-directions.md"

    def test_decomposer_prompt_mentions_directions(self) -> None:
        wf = deep_research_workflow()
        node = wf.nodes["decomposer"]
        assert isinstance(node, AgentNode)
        assert "research directions" in node.prompt_template.lower()
        assert "3-5" in node.prompt_template

    def test_deep_researcher_is_agent_node(self) -> None:
        wf = deep_research_workflow()
        node = wf.nodes["deep_researcher"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.RESEARCHER

    def test_deep_researcher_has_post_check(self) -> None:
        wf = deep_research_workflow()
        node = wf.nodes["deep_researcher"]
        assert isinstance(node, AgentNode)
        assert len(node.post_checks) == 1
        assert node.post_checks[0].must_exist is True
        assert node.post_checks[0].min_size == 500
        assert node.post_checks[0].path == ".factory/strategy/research-combined.md"

    def test_deep_researcher_reads_directions(self) -> None:
        wf = deep_research_workflow()
        node = wf.nodes["deep_researcher"]
        assert isinstance(node, AgentNode)
        assert ".factory/strategy/research-directions.md" in node.reads
        assert ".factory/strategy/observations.md" in node.reads

    def test_deep_researcher_timeout(self) -> None:
        wf = deep_research_workflow()
        node = wf.nodes["deep_researcher"]
        assert isinstance(node, AgentNode)
        assert node.timeout == 1800

    def test_deep_researcher_prompt_contains_protocol(self) -> None:
        wf = deep_research_workflow()
        node = wf.nodes["deep_researcher"]
        assert isinstance(node, AgentNode)
        prompt = node.prompt_template
        assert "Phase 1: Internal Research" in prompt
        assert "Phase 2: Read Research Directions" in prompt
        assert "research-directions.md" in prompt

    def test_deep_researcher_writes_combined_report(self) -> None:
        wf = deep_research_workflow()
        node = wf.nodes["deep_researcher"]
        assert ".factory/strategy/research-combined.md" in node.writes

    def test_gate_coverage_is_ceo_agent(self) -> None:
        wf = deep_research_workflow()
        gate = wf.nodes["gate_coverage"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "agent"
        assert gate.evaluator_role == AgentRole.CEO

    def test_gate_coverage_reads_directions_and_report(self) -> None:
        wf = deep_research_workflow()
        gate = wf.nodes["gate_coverage"]
        assert isinstance(gate, GateNode)
        assert ".factory/strategy/research-directions.md" in gate.reads
        assert ".factory/strategy/research-combined.md" in gate.reads

    def test_gate_prompt_checks_per_direction_coverage(self) -> None:
        wf = deep_research_workflow()
        gate = wf.nodes["gate_coverage"]
        assert isinstance(gate, GateNode)
        prompt = gate.gate_prompt
        assert "research-directions.md" in prompt
        assert "research-combined.md" in prompt
        assert "PROCEED" in prompt
        assert "RELOOP" in prompt



# ── Edge wiring ────────────────────────────────────────────────


class TestDeepResearchEdges:
    def test_study_to_decomposer_edge(self) -> None:
        wf = deep_research_workflow()
        assert any(
            e.source == "study"
            and e.target == "decomposer"
            and e.condition is None
            for e in wf.edges
        )

    def test_decomposer_to_deep_researcher_edge(self) -> None:
        wf = deep_research_workflow()
        assert any(
            e.source == "decomposer"
            and e.target == "deep_researcher"
            and e.condition is None
            for e in wf.edges
        )

    def test_deep_researcher_to_gate_edge(self) -> None:
        wf = deep_research_workflow()
        assert any(
            e.source == "deep_researcher"
            and e.target == "gate_coverage"
            and e.condition is None
            for e in wf.edges
        )

    def test_gate_proceed_is_terminal(self) -> None:
        wf = deep_research_workflow()
        assert not any(
            e.source == "gate_coverage"
            and e.condition == VerdictType.PROCEED
            for e in wf.edges
        )

    def test_gate_reloop_to_deep_researcher(self) -> None:
        wf = deep_research_workflow()
        assert any(
            e.source == "gate_coverage"
            and e.target == "deep_researcher"
            and e.condition == VerdictType.RELOOP
            for e in wf.edges
        )

    def test_reloop_not_to_decomposer(self) -> None:
        """RELOOP goes to deep_researcher, NOT decomposer."""
        wf = deep_research_workflow()
        assert not any(
            e.source == "gate_coverage"
            and e.target == "decomposer"
            for e in wf.edges
        )

    def test_total_edge_count(self) -> None:
        wf = deep_research_workflow()
        assert len(wf.edges) == 4


# ── Trigger function ──────────────────────────────────────────────


class TestDeepResearchTrigger:
    def test_trigger_fires_for_deep_research_mode(self) -> None:
        wf = deep_research_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "deep-research"})

    def test_trigger_requires_has_factory(self) -> None:
        wf = deep_research_workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.NO_REPO, {"mode": "deep-research"})
        assert not wf.trigger(ProjectState.NO_FACTORY, {"mode": "deep-research"})

    def test_trigger_does_not_fire_for_other_modes(self) -> None:
        wf = deep_research_workflow()
        assert wf.trigger is not None
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "research"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "founder"})


# ── Registration ──────────────────────────────────────────────────


class TestDeepResearchRegistration:
    def test_registered_in_builtin_registry(self) -> None:
        reg = _get_builtin_registry()
        assert "deep-research" in reg

    def test_registered_in_register_all(self) -> None:
        all_wf = register_all()
        assert "deep-research" in all_wf

    def test_registered_workflow_is_valid(self) -> None:
        all_wf = register_all()
        wf = all_wf["deep-research"]
        issues = wf.validate_graph()
        assert issues == [], f"registered deep-research has issues: {issues}"


# ── Skill export ──────────────────────────────────────────────────


class TestDeepResearchSkillExport:
    def test_workflow_meta_entry_exists(self) -> None:
        from factory.workflow.skill_export import WORKFLOW_META

        assert "deep-research" in WORKFLOW_META
        assert "description" in WORKFLOW_META["deep-research"]

    def test_skill_md_generation(self) -> None:
        from factory.workflow.skill_export import workflow_to_skill_md

        wf = deep_research_workflow()
        skill_md = workflow_to_skill_md(wf)
        assert "workflow-deep-research" in skill_md
        assert "deep_researcher" in skill_md

    def test_skill_md_contains_gate(self) -> None:
        from factory.workflow.skill_export import workflow_to_skill_md

        wf = deep_research_workflow()
        skill_md = workflow_to_skill_md(wf)
        assert "gate_coverage" in skill_md.lower() or "Coverage" in skill_md

    def test_skill_md_no_fork_join(self) -> None:
        """v4: SKILL.md should not contain fork/join instructions."""
        from factory.workflow.skill_export import workflow_to_skill_md

        wf = deep_research_workflow()
        skill_md = workflow_to_skill_md(wf)
        assert "fork_research" not in skill_md
        assert "join_research" not in skill_md


# ── Existing workflows unchanged ──────────────────────────────────


class TestExistingWorkflowsUnchanged:
    """Verify that adding deep-research did NOT modify existing workflows."""

    def test_build_still_uses_fork_join(self) -> None:
        from factory.workflow.definitions import build_workflow
        from factory.workflow.primitives import ForkNode, JoinNode

        wf = build_workflow()
        assert "fork_research" in wf.nodes
        assert "join_research" in wf.nodes
        assert isinstance(wf.nodes["fork_research"], ForkNode)
        assert isinstance(wf.nodes["join_research"], JoinNode)
        assert wf.start_node == "fork_research"

    def test_build_researchers_unchanged(self) -> None:
        from factory.workflow.definitions import build_workflow

        wf = build_workflow()
        expected = {"researcher_similar", "researcher_techstack", "researcher_pitfalls"}
        actual = {nid for nid in wf.nodes if nid.startswith("researcher_")}
        assert expected == actual

    def test_create_still_uses_fork_join(self) -> None:
        from factory.workflow.definitions import create_workflow
        from factory.workflow.primitives import ForkNode, JoinNode

        wf = create_workflow()
        assert "fork_research" in wf.nodes
        assert "join_research" in wf.nodes
        assert isinstance(wf.nodes["fork_research"], ForkNode)
        assert isinstance(wf.nodes["join_research"], JoinNode)

    def test_design_still_uses_fork_join(self) -> None:
        from factory.workflow.definitions import design_workflow

        wf = design_workflow()
        assert "fork_research" in wf.nodes
        assert "join_research" in wf.nodes

    def test_research_standalone_unchanged(self) -> None:
        all_wf = register_all()
        wf = all_wf["research-standalone"]
        assert "fork_research" in wf.nodes
        assert wf.start_node == "fork_research"

    def test_improve_unchanged(self) -> None:
        from factory.workflow.definitions import improve_workflow

        wf = improve_workflow()
        assert "researcher" in wf.nodes
        assert wf.start_node == "study"
        issues = wf.validate_graph()
        assert issues == [], f"improve workflow broken: {issues}"

    def test_founder_unchanged(self) -> None:
        from factory.workflow.definitions import founder_workflow

        wf = founder_workflow()
        assert wf.start_node == "study"
        assert wf.terminal is True
        issues = wf.validate_graph()
        assert issues == [], f"founder workflow broken: {issues}"
