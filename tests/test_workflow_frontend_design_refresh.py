"""Tests for the frontend-design-refresh workflow (W₁₅)."""

from __future__ import annotations

from factory.models import ProjectState
from factory.workflow.definitions import frontend_design_refresh_workflow, register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    VerdictType,
)


# ── Graph Validation ────────────────────────────────────────────


class TestRefreshValid:
    def test_validates_cleanly(self) -> None:
        wf = frontend_design_refresh_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"frontend-design-refresh has issues: {issues}"

    def test_name(self) -> None:
        wf = frontend_design_refresh_workflow()
        assert wf.name == "frontend-design-refresh"

    def test_node_count(self) -> None:
        wf = frontend_design_refresh_workflow()
        assert len(wf.nodes) == 14

    def test_start_node(self) -> None:
        wf = frontend_design_refresh_workflow()
        assert wf.start_node == "fork_refresh_research"

    def test_registered(self) -> None:
        all_wf = register_all()
        assert "frontend-design-refresh" in all_wf


# ── Trigger ─────────────────────────────────────────────────────


class TestRefreshTrigger:
    def test_matches_explicit_mode(self) -> None:
        wf = frontend_design_refresh_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "frontend-design-refresh"})
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "frontend-design-refresh"})

    def test_rejects_other_modes(self) -> None:
        wf = frontend_design_refresh_workflow()
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "frontend-design"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "frontend-design-discover"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


# ── Research Phase ────────────────────────────────────────────


class TestRefreshResearchPhase:
    def test_fork_has_five_researchers(self) -> None:
        wf = frontend_design_refresh_workflow()
        fork = wf.nodes["fork_refresh_research"]
        assert isinstance(fork, ForkNode)
        assert set(fork.targets) == {
            "researcher_tokens",
            "researcher_components",
            "researcher_patterns",
            "researcher_ux",
            "researcher_infra",
        }

    def test_fork_includes_infra(self) -> None:
        wf = frontend_design_refresh_workflow()
        fork = wf.nodes["fork_refresh_research"]
        assert "researcher_infra" in fork.targets

    def test_researchers_are_researcher_role(self) -> None:
        wf = frontend_design_refresh_workflow()
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
        wf = frontend_design_refresh_workflow()
        join = wf.nodes["join_refresh_research"]
        assert isinstance(join, JoinNode)
        assert set(join.sources) == {
            "researcher_tokens",
            "researcher_components",
            "researcher_patterns",
            "researcher_ux",
            "researcher_infra",
        }


# ── Auditor Phase ─────────────────────────────────────────────


class TestRefreshAuditorPhase:
    def test_auditor_is_strategist(self) -> None:
        wf = frontend_design_refresh_workflow()
        node = wf.nodes["refresh_auditor"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST

    def test_auditor_writes_staging_files(self) -> None:
        wf = frontend_design_refresh_workflow()
        node = wf.nodes["refresh_auditor"]
        assert ".factory/design-system/design-baseline.json.new" in node.writes
        assert ".factory/design-system/rules.md.new" in node.writes

    def test_audit_gate_reloops_to_auditor(self) -> None:
        wf = frontend_design_refresh_workflow()
        gate = wf.nodes["gate_refresh_audit"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_role == AgentRole.CEO
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_refresh_audit" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1
        assert reloop_edges[0].target == "refresh_auditor"


# ── Differ Phase ──────────────────────────────────────────────


class TestRefreshDifferPhase:
    def test_differ_is_strategist(self) -> None:
        wf = frontend_design_refresh_workflow()
        node = wf.nodes["refresh_differ"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST

    def test_differ_writes_changeset(self) -> None:
        wf = frontend_design_refresh_workflow()
        node = wf.nodes["refresh_differ"]
        assert ".factory/design-system/refresh-changeset.md" in node.writes

    def test_user_gate_exists(self) -> None:
        wf = frontend_design_refresh_workflow()
        gate = wf.nodes["gate_refresh_approve"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "user"

    def test_user_gate_proceed_to_applier(self) -> None:
        wf = frontend_design_refresh_workflow()
        proceed_edges = [
            e for e in wf.edges
            if e.source == "gate_refresh_approve" and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed_edges) == 1
        assert proceed_edges[0].target == "refresh_applier"

    def test_user_gate_reloop_to_differ(self) -> None:
        wf = frontend_design_refresh_workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_refresh_approve" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1
        assert reloop_edges[0].target == "refresh_differ"

    def test_user_gate_halt_to_archivist(self) -> None:
        wf = frontend_design_refresh_workflow()
        halt_edges = [
            e for e in wf.edges
            if e.source == "gate_refresh_approve" and e.condition == VerdictType.HALT
        ]
        assert len(halt_edges) == 1
        assert halt_edges[0].target == "archivist_refresh"


# ── Applier Phase ─────────────────────────────────────────────


class TestRefreshApplierPhase:
    def test_applier_is_fn_node(self) -> None:
        wf = frontend_design_refresh_workflow()
        node = wf.nodes["refresh_applier"]
        assert isinstance(node, FnNode)

    def test_applier_writes_final_files(self) -> None:
        wf = frontend_design_refresh_workflow()
        node = wf.nodes["refresh_applier"]
        assert ".factory/design-system/design-baseline.json" in node.writes
        assert ".factory/design-system/rules.md" in node.writes


# ── Terminal Node ─────────────────────────────────────────────


class TestRefreshTerminal:
    def test_archivist_is_nonblocking(self) -> None:
        wf = frontend_design_refresh_workflow()
        node = wf.nodes["archivist_refresh"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.ARCHIVIST
        assert node.blocking is False


# ── Edge Completeness ─────────────────────────────────────────


class TestRefreshEdgeCompleteness:
    def test_no_dangling_edges(self) -> None:
        wf = frontend_design_refresh_workflow()
        node_ids = set(wf.nodes.keys())
        for edge in wf.edges:
            assert edge.source in node_ids, f"dangling source: {edge.source}"
            assert edge.target in node_ids, f"dangling target: {edge.target}"

    def test_all_gates_have_proceed(self) -> None:
        wf = frontend_design_refresh_workflow()
        gate_ids = [
            nid for nid, node in wf.nodes.items()
            if isinstance(node, GateNode)
        ]
        for gate_id in gate_ids:
            gate_edges = [e for e in wf.edges if e.source == gate_id]
            conditions = {e.condition for e in gate_edges}
            assert VerdictType.PROCEED in conditions, f"{gate_id} missing PROCEED edge"
