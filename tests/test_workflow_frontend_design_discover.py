"""Tests for the frontend-design-discover workflow (W₁₄)."""

from __future__ import annotations


from factory.models import ProjectState
from factory.workflow.definitions import (
    frontend_design_discover_workflow,
    register_all,
)
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ForkNode,
    GateNode,
    JoinNode,
    VerdictType,
)


# ── Graph Validation ────────────────────────────────────────────


class TestDiscoverValid:
    def test_validates_cleanly(self) -> None:
        wf = frontend_design_discover_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"frontend-design-discover has issues: {issues}"

    def test_name(self) -> None:
        wf = frontend_design_discover_workflow()
        assert wf.name == "frontend-design-discover"

    def test_node_count(self) -> None:
        wf = frontend_design_discover_workflow()
        assert len(wf.nodes) == 11

    def test_start_node(self) -> None:
        wf = frontend_design_discover_workflow()
        assert wf.start_node == "fork_discover_research"

    def test_registered(self) -> None:
        all_wf = register_all()
        assert "frontend-design-discover" in all_wf


# ── Trigger ─────────────────────────────────────────────────────


class TestDiscoverTrigger:
    def test_matches_explicit_mode(self) -> None:
        wf = frontend_design_discover_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "frontend-design-discover"})
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "frontend-design-discover"})

    def test_rejects_other_modes(self) -> None:
        wf = frontend_design_discover_workflow()
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "frontend-design"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "design"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


# ── Research Phase ────────────────────────────────────────────


class TestDiscoverResearchPhase:
    def test_fork_has_five_researchers(self) -> None:
        wf = frontend_design_discover_workflow()
        fork = wf.nodes["fork_discover_research"]
        assert isinstance(fork, ForkNode)
        assert set(fork.targets) == {
            "researcher_tokens",
            "researcher_components",
            "researcher_patterns",
            "researcher_ux",
            "researcher_infra",
        }

    def test_researchers_are_researcher_role(self) -> None:
        wf = frontend_design_discover_workflow()
        for nid in [
            "researcher_tokens",
            "researcher_components",
            "researcher_patterns",
            "researcher_ux",
            "researcher_infra",
        ]:
            node = wf.nodes[nid]
            assert isinstance(node, AgentNode)
            assert node.role == AgentRole.RESEARCHER

    def test_join_matches_fork(self) -> None:
        wf = frontend_design_discover_workflow()
        join = wf.nodes["join_discover_research"]
        assert isinstance(join, JoinNode)
        assert set(join.sources) == {
            "researcher_tokens",
            "researcher_components",
            "researcher_patterns",
            "researcher_ux",
            "researcher_infra",
        }


# ── Auditor Phase ─────────────────────────────────────────────


class TestDiscoverAuditorPhase:
    def test_auditor_is_strategist(self) -> None:
        wf = frontend_design_discover_workflow()
        node = wf.nodes["design_auditor"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST

    def test_auditor_writes_baseline_and_rules(self) -> None:
        wf = frontend_design_discover_workflow()
        node = wf.nodes["design_auditor"]
        assert ".factory/design-system/design-baseline.json" in node.writes
        assert ".factory/design-system/rules.md" in node.writes

    def test_auditor_reads_infra_context(self) -> None:
        wf = frontend_design_discover_workflow()
        node = wf.nodes["design_auditor"]
        assert ".factory/design-system/infra-context.md" in node.reads

    def test_auditor_reads_all_research_artifacts(self) -> None:
        wf = frontend_design_discover_workflow()
        node = wf.nodes["design_auditor"]
        expected = {
            ".factory/design-system/token-audit.md",
            ".factory/design-system/component-inventory.md",
            ".factory/design-system/pattern-library.md",
            ".factory/design-system/ux-patterns.md",
            ".factory/design-system/infra-context.md",
        }
        assert expected <= node.reads

    def test_audit_gate_reloops_to_auditor(self) -> None:
        wf = frontend_design_discover_workflow()
        gate = wf.nodes["gate_discover_audit"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_role == AgentRole.CEO
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_discover_audit" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1
        assert reloop_edges[0].target == "design_auditor"


# ── No Builder / Spec / User Gates ────────────────────────────


class TestDiscoverNoBuilderNodes:
    """Discover workflow must NOT contain builder, spec, or user gates."""

    def test_no_builder(self) -> None:
        wf = frontend_design_discover_workflow()
        assert "builder" not in wf.nodes

    def test_no_spec_writer(self) -> None:
        wf = frontend_design_discover_workflow()
        assert "spec_writer" not in wf.nodes

    def test_no_user_gate(self) -> None:
        wf = frontend_design_discover_workflow()
        for nid, node in wf.nodes.items():
            if hasattr(node, "evaluator_type"):
                assert node.evaluator_type != "user", f"{nid} is a user gate"


# ── Terminal Node ─────────────────────────────────────────────


class TestDiscoverTerminal:
    def test_archivist_is_nonblocking(self) -> None:
        wf = frontend_design_discover_workflow()
        node = wf.nodes["archivist_discover"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.ARCHIVIST
        assert node.blocking is False

    def test_archivist_writes_archive(self) -> None:
        wf = frontend_design_discover_workflow()
        node = wf.nodes["archivist_discover"]
        assert ".factory/archive/design-discover.md" in node.writes


# ── Edge Completeness ─────────────────────────────────────────


class TestDiscoverEdgeCompleteness:
    def test_no_dangling_edges(self) -> None:
        wf = frontend_design_discover_workflow()
        node_ids = set(wf.nodes.keys())
        for edge in wf.edges:
            assert edge.source in node_ids, f"dangling source: {edge.source}"
            assert edge.target in node_ids, f"dangling target: {edge.target}"

    def test_research_gate_has_proceed_and_reloop(self) -> None:
        wf = frontend_design_discover_workflow()
        gate_edges = [e for e in wf.edges if e.source == "gate_discover_research"]
        conditions = {e.condition for e in gate_edges}
        assert VerdictType.PROCEED in conditions
        assert VerdictType.RELOOP in conditions

    def test_audit_gate_has_proceed_and_reloop(self) -> None:
        wf = frontend_design_discover_workflow()
        gate_edges = [e for e in wf.edges if e.source == "gate_discover_audit"]
        conditions = {e.condition for e in gate_edges}
        assert VerdictType.PROCEED in conditions
        assert VerdictType.RELOOP in conditions
