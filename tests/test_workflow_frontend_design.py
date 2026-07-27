"""Tests for the frontend-design workflow (W₁₂)."""

from __future__ import annotations

import pytest

from factory.models import ProjectState
from factory.workflow.definitions import (
    frontend_design_workflow,
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


class TestFrontendDesignValid:
    def test_validates_cleanly(self) -> None:
        wf = frontend_design_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"frontend-design has issues: {issues}"

    def test_name(self) -> None:
        wf = frontend_design_workflow()
        assert wf.name == "frontend-design"

    def test_node_count(self) -> None:
        wf = frontend_design_workflow()
        assert len(wf.nodes) == 20

    def test_start_node(self) -> None:
        wf = frontend_design_workflow()
        assert wf.start_node == "fork_design_research"

    def test_registered(self) -> None:
        all_wf = register_all()
        assert "frontend-design" in all_wf


# ── Trigger ─────────────────────────────────────────────────────


class TestFrontendDesignTrigger:
    def test_matches_explicit_mode(self) -> None:
        wf = frontend_design_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "frontend-design"})
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "frontend-design"})

    def test_rejects_other_modes(self) -> None:
        wf = frontend_design_workflow()
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "design"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})


# ── Phase 1: Design Research ────────────────────────────────────


class TestDesignResearchPhase:
    def test_fork_has_three_researchers(self) -> None:
        wf = frontend_design_workflow()
        fork = wf.nodes["fork_design_research"]
        assert isinstance(fork, ForkNode)
        assert set(fork.targets) == {
            "researcher_tokens",
            "researcher_components",
            "researcher_patterns",
        }

    def test_researchers_are_researcher_role(self) -> None:
        wf = frontend_design_workflow()
        for nid in ["researcher_tokens", "researcher_components", "researcher_patterns"]:
            node = wf.nodes[nid]
            assert isinstance(node, AgentNode)
            assert node.role == AgentRole.RESEARCHER

    def test_join_matches_fork(self) -> None:
        wf = frontend_design_workflow()
        join = wf.nodes["join_design_research"]
        assert isinstance(join, JoinNode)
        assert set(join.sources) == {
            "researcher_tokens",
            "researcher_components",
            "researcher_patterns",
        }

    def test_research_gate_is_ceo(self) -> None:
        wf = frontend_design_workflow()
        gate = wf.nodes["gate_research"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "agent"
        assert gate.evaluator_role == AgentRole.CEO

    def test_research_gate_reloops_to_fork(self) -> None:
        wf = frontend_design_workflow()
        reloop = [
            e
            for e in wf.edges
            if e.source == "gate_research" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop) == 1
        assert reloop[0].target == "fork_design_research"


# ── Phase 2: Auditor ────────────────────────────────────────────


class TestAuditorPhase:
    def test_auditor_is_strategist(self) -> None:
        wf = frontend_design_workflow()
        node = wf.nodes["design_auditor"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST

    def test_auditor_writes_baseline_and_rules(self) -> None:
        wf = frontend_design_workflow()
        node = wf.nodes["design_auditor"]
        assert ".factory/design-system/design-baseline.json" in node.writes
        assert ".factory/design-system/rules.md" in node.writes

    def test_audit_gate_reloops_to_auditor(self) -> None:
        wf = frontend_design_workflow()
        reloop = [
            e
            for e in wf.edges
            if e.source == "gate_audit" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop) == 1
        assert reloop[0].target == "design_auditor"


# ── Phase 3: Spec + User Gate ───────────────────────────────────


class TestSpecPhase:
    def test_spec_writer_is_strategist(self) -> None:
        wf = frontend_design_workflow()
        node = wf.nodes["spec_writer"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.STRATEGIST

    def test_spec_writer_writes_ui_spec(self) -> None:
        wf = frontend_design_workflow()
        node = wf.nodes["spec_writer"]
        assert ".factory/design-system/ui-spec.md" in node.writes

    def test_spec_gate_is_user(self) -> None:
        wf = frontend_design_workflow()
        gate = wf.nodes["gate_spec"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "user"

    def test_spec_gate_reloops_to_writer(self) -> None:
        wf = frontend_design_workflow()
        reloop = [
            e
            for e in wf.edges
            if e.source == "gate_spec" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop) == 1
        assert reloop[0].target == "spec_writer"


# ── Phase 4: Builder ────────────────────────────────────────────


class TestBuilderPhase:
    def test_builder_is_builder_role(self) -> None:
        wf = frontend_design_workflow()
        node = wf.nodes["builder"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.BUILDER

    def test_builder_reads_design_artifacts(self) -> None:
        wf = frontend_design_workflow()
        node = wf.nodes["builder"]
        assert ".factory/design-system/ui-spec.md" in node.reads
        assert ".factory/design-system/design-baseline.json" in node.reads
        assert ".factory/design-system/rules.md" in node.reads

    def test_build_gate_is_fn(self) -> None:
        wf = frontend_design_workflow()
        gate = wf.nodes["gate_build"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "fn"

    def test_build_gate_reloops_to_builder(self) -> None:
        wf = frontend_design_workflow()
        reloop = [
            e
            for e in wf.edges
            if e.source == "gate_build" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop) == 1
        assert reloop[0].target == "builder"


# ── Phase 5: Design QA ──────────────────────────────────────────


class TestDesignQA:
    def test_health_checker_exists(self) -> None:
        wf = frontend_design_workflow()
        node = wf.nodes["health_checker"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.HEALTH_CHECKER

    def test_code_reviewer_exists(self) -> None:
        wf = frontend_design_workflow()
        node = wf.nodes["code_reviewer"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.CODE_REVIEWER

    def test_review_gate_checks_critical_found(self) -> None:
        wf = frontend_design_workflow()
        gate = wf.nodes["gate_review"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "fn"
        assert "CRITICAL_FOUND" in gate.evaluator_command

    def test_consistency_tester_is_adversarial(self) -> None:
        wf = frontend_design_workflow()
        node = wf.nodes["consistency_tester"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.ADVERSARIAL_TESTER

    def test_consistency_tester_writes_report(self) -> None:
        wf = frontend_design_workflow()
        node = wf.nodes["consistency_tester"]
        assert ".factory/design-system/consistency-report.json" in node.writes

    def test_consistency_gate_reloops_to_builder(self) -> None:
        wf = frontend_design_workflow()
        reloop = [
            e
            for e in wf.edges
            if e.source == "gate_consistency" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop) == 1
        assert reloop[0].target == "builder"

    def test_qa_flow_order(self) -> None:
        """health_checker → code_reviewer → gate_review → consistency_tester."""
        wf = frontend_design_workflow()
        edges_by_source = {e.source: e for e in wf.edges if e.condition is None}
        assert edges_by_source["health_checker"].target == "code_reviewer"
        assert edges_by_source["code_reviewer"].target == "gate_review"
        proceed = [
            e
            for e in wf.edges
            if e.source == "gate_review" and e.condition == VerdictType.PROCEED
        ]
        assert len(proceed) == 1
        assert proceed[0].target == "consistency_tester"


# ── Terminal Nodes ──────────────────────────────────────────────


class TestTerminalNodes:
    def test_archivist_is_nonblocking(self) -> None:
        wf = frontend_design_workflow()
        node = wf.nodes["archivist_build"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.ARCHIVIST
        assert node.blocking is False

    def test_only_archivist_is_nonblocking(self) -> None:
        wf = frontend_design_workflow()
        nonblocking = [
            nid for nid, node in wf.nodes.items() if hasattr(node, "blocking") and not node.blocking
        ]
        assert nonblocking == ["archivist_build"]

    def test_precheck_routes_to_archivist(self) -> None:
        wf = frontend_design_workflow()
        proceed = [
            e
            for e in wf.edges
            if e.source == "gate_precheck" and e.condition == VerdictType.PROCEED
        ]
        halt = [
            e
            for e in wf.edges
            if e.source == "gate_precheck" and e.condition == VerdictType.HALT
        ]
        assert len(proceed) == 1
        assert proceed[0].target == "archivist_build"
        assert len(halt) == 1
        assert halt[0].target == "archivist_build"


# ── Edge Completeness ───────────────────────────────────────────


class TestEdgeCompleteness:
    def test_no_dangling_edges(self) -> None:
        wf = frontend_design_workflow()
        node_ids = set(wf.nodes.keys())
        for edge in wf.edges:
            assert edge.source in node_ids, f"dangling source: {edge.source}"
            assert edge.target in node_ids, f"dangling target: {edge.target}"

    def test_every_gate_has_proceed(self) -> None:
        wf = frontend_design_workflow()
        for nid, node in wf.nodes.items():
            if isinstance(node, GateNode):
                proceed = [
                    e
                    for e in wf.edges
                    if e.source == nid and e.condition == VerdictType.PROCEED
                ]
                assert len(proceed) >= 1, f"gate {nid} has no PROCEED edge"

    def test_every_reloop_gate_has_reloop_edge(self) -> None:
        wf = frontend_design_workflow()
        gates_with_reloop = [
            "gate_research",
            "gate_audit",
            "gate_spec",
            "gate_build",
            "gate_consistency",
            "gate_doc_freshness",
        ]
        for gid in gates_with_reloop:
            reloop = [
                e
                for e in wf.edges
                if e.source == gid and e.condition == VerdictType.RELOOP
            ]
            assert len(reloop) == 1, f"gate {gid} should have exactly 1 RELOOP edge"
