"""Tests for the frontend-design-scan workflow (W₁₃)."""

from __future__ import annotations


from factory.models import ProjectState
from factory.workflow.definitions import (
    frontend_design_scan_workflow,
    register_all,
)
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


class TestFrontendDesignScanValid:
    def test_validates_cleanly(self) -> None:
        wf = frontend_design_scan_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"frontend-design-scan has issues: {issues}"

    def test_name(self) -> None:
        wf = frontend_design_scan_workflow()
        assert wf.name == "frontend-design-scan"

    def test_node_count(self) -> None:
        wf = frontend_design_scan_workflow()
        assert len(wf.nodes) == 21

    def test_start_node(self) -> None:
        wf = frontend_design_scan_workflow()
        assert wf.start_node == "fork_scan_research"

    def test_registered(self) -> None:
        all_wf = register_all()
        assert "frontend-design-scan" in all_wf


# ── Trigger ─────────────────────────────────────────────────────


class TestScanTrigger:
    def test_matches_explicit_mode(self) -> None:
        wf = frontend_design_scan_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "frontend-design-scan"})
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "frontend-design-scan"})

    def test_rejects_other_modes(self) -> None:
        wf = frontend_design_scan_workflow()
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "frontend-design"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


# ── Phase 1: Research ──────────────────────────────────────────


class TestScanResearchPhase:
    def test_fork_has_four_researchers(self) -> None:
        wf = frontend_design_scan_workflow()
        fork = wf.nodes["fork_scan_research"]
        assert isinstance(fork, ForkNode)
        assert set(fork.targets) == {
            "researcher_tokens",
            "researcher_components",
            "researcher_patterns",
            "researcher_ux",
        }

    def test_researchers_are_researcher_role(self) -> None:
        wf = frontend_design_scan_workflow()
        for nid in ["researcher_tokens", "researcher_components",
                     "researcher_patterns", "researcher_ux"]:
            node = wf.nodes[nid]
            assert isinstance(node, AgentNode)
            assert node.role == AgentRole.RESEARCHER

    def test_join_matches_fork(self) -> None:
        wf = frontend_design_scan_workflow()
        join = wf.nodes["join_scan_research"]
        assert isinstance(join, JoinNode)
        assert set(join.sources) == {
            "researcher_tokens",
            "researcher_components",
            "researcher_patterns",
            "researcher_ux",
        }


# ── Phase 2: Auditor ──────────────────────────────────────────


class TestScanAuditorPhase:
    def test_auditor_is_strategist(self) -> None:
        wf = frontend_design_scan_workflow()
        node = wf.nodes["scan_auditor"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST

    def test_auditor_writes_baseline(self) -> None:
        wf = frontend_design_scan_workflow()
        node = wf.nodes["scan_auditor"]
        assert ".factory/design-system/design-baseline.json" in node.writes


# ── Phase 3: Check Scripts ─────────────────────────────────────


class TestScanCheckPhase:
    def test_fork_has_six_checks(self) -> None:
        wf = frontend_design_scan_workflow()
        fork = wf.nodes["fork_scan_checks"]
        assert isinstance(fork, ForkNode)
        assert len(fork.targets) == 6

    def test_all_checks_are_fn_nodes(self) -> None:
        wf = frontend_design_scan_workflow()
        fork = wf.nodes["fork_scan_checks"]
        for target in fork.targets:
            node = wf.nodes[target]
            assert isinstance(node, FnNode)

    def test_all_checks_use_scan_mode_full(self) -> None:
        wf = frontend_design_scan_workflow()
        fork = wf.nodes["fork_scan_checks"]
        for target in fork.targets:
            node = wf.nodes[target]
            assert isinstance(node, FnNode)
            assert "SCAN_MODE=full" in node.command

    def test_join_matches_fork(self) -> None:
        wf = frontend_design_scan_workflow()
        fork = wf.nodes["fork_scan_checks"]
        join = wf.nodes["join_scan_checks"]
        assert isinstance(join, JoinNode)
        assert set(join.sources) == set(fork.targets)


# ── Phase 4: Health Report ─────────────────────────────────────


class TestScanReportPhase:
    def test_health_report_writer_is_strategist(self) -> None:
        wf = frontend_design_scan_workflow()
        node = wf.nodes["health_report_writer"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST

    def test_health_report_writer_writes_report(self) -> None:
        wf = frontend_design_scan_workflow()
        node = wf.nodes["health_report_writer"]
        assert ".factory/design-system/health-report.json" in node.writes

    def test_archivist_is_nonblocking(self) -> None:
        wf = frontend_design_scan_workflow()
        node = wf.nodes["archivist_scan"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.ARCHIVIST
        assert node.blocking is False


# ── Compliance Plan + Fix Pipeline ─────────────────────────────


class TestScanCompliancePipeline:
    """Scan workflow has compliance planner, user gate, builder, and build gate."""

    def test_no_spec_writer(self) -> None:
        wf = frontend_design_scan_workflow()
        assert "spec_writer" not in wf.nodes

    def test_compliance_planner_exists(self) -> None:
        wf = frontend_design_scan_workflow()
        node = wf.nodes["compliance_planner"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST

    def test_compliance_planner_writes_plan(self) -> None:
        wf = frontend_design_scan_workflow()
        node = wf.nodes["compliance_planner"]
        assert ".factory/design-system/compliance-plan.md" in node.writes

    def test_user_gate_exists(self) -> None:
        wf = frontend_design_scan_workflow()
        gate = wf.nodes["gate_compliance_approve"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "user"

    def test_user_gate_proceed_to_builder(self) -> None:
        wf = frontend_design_scan_workflow()
        proceed = [
            e for e in wf.edges
            if e.source == "gate_compliance_approve" and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed) == 1
        assert proceed[0].target == "compliance_builder"

    def test_user_gate_halt_to_archivist(self) -> None:
        wf = frontend_design_scan_workflow()
        halt = [
            e for e in wf.edges
            if e.source == "gate_compliance_approve" and e.condition == VerdictType.HALT
        ]
        assert len(halt) == 1
        assert halt[0].target == "archivist_scan"

    def test_compliance_builder_exists(self) -> None:
        wf = frontend_design_scan_workflow()
        node = wf.nodes["compliance_builder"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.BUILDER

    def test_build_gate_is_fn(self) -> None:
        wf = frontend_design_scan_workflow()
        gate = wf.nodes["gate_compliance_build"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "fn"

    def test_build_gate_reloops_to_builder(self) -> None:
        wf = frontend_design_scan_workflow()
        reloop = [
            e for e in wf.edges
            if e.source == "gate_compliance_build" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop) == 1
        assert reloop[0].target == "compliance_builder"


# ── Edge Completeness ──────────────────────────────────────────


class TestScanEdgeCompleteness:
    def test_no_dangling_edges(self) -> None:
        wf = frontend_design_scan_workflow()
        node_ids = set(wf.nodes.keys())
        for edge in wf.edges:
            assert edge.source in node_ids, f"dangling source: {edge.source}"
            assert edge.target in node_ids, f"dangling target: {edge.target}"
