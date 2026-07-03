"""Tests for deep-qa mode: Workflow.subgraph(), deep-qa workflow structure, CLI parser."""

from __future__ import annotations

import subprocess
import sys

import pytest

from factory.workflow.definitions import improve_workflow, register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    GateNode,
    VerdictType,
)


# ── Workflow.subgraph() ─────────────────────────────────────────


class TestSubgraph:
    def test_extracts_requested_nodes(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"health_checker", "code_reviewer"}, name="test", start_node="health_checker")
        assert set(sub.nodes.keys()) == {"health_checker", "code_reviewer"}

    def test_filters_edges(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"health_checker", "code_reviewer"}, name="test", start_node="health_checker")
        for edge in sub.edges:
            assert edge.source in sub.nodes
            assert edge.target in sub.nodes

    def test_deep_copies_nodes(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"health_checker", "code_reviewer"}, name="test", start_node="health_checker")
        assert sub.nodes["health_checker"] is not wf.nodes["health_checker"]

    def test_sets_name_and_start_node(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"health_checker", "code_reviewer"}, name="myname", start_node="health_checker")
        assert sub.name == "myname"
        assert sub.start_node == "health_checker"

    def test_missing_node_raises(self) -> None:
        wf = improve_workflow()
        with pytest.raises(ValueError, match="node 'nonexistent'"):
            wf.subgraph({"nonexistent"}, name="test", start_node="nonexistent")

    def test_preserves_edge_between_included_nodes(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph(
            {"health_checker", "code_reviewer", "gate_review"}, name="test", start_node="health_checker",
        )
        edge_pairs = {(e.source, e.target) for e in sub.edges}
        assert ("health_checker", "code_reviewer") in edge_pairs
        assert ("code_reviewer", "gate_review") in edge_pairs

    def test_excludes_edges_to_outside_nodes(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"health_checker", "code_reviewer"}, name="test", start_node="health_checker")
        for edge in sub.edges:
            assert edge.target != "gate_review"
            assert edge.target != "builder"


# ── deep-qa workflow structure ─────────────────────────────────


class TestDeepQaWorkflow:
    def _get_wf(self):
        from factory.workflow.contributed.deep_qa import workflow
        return workflow()

    def test_valid_graph(self) -> None:
        wf = self._get_wf()
        issues = wf.validate_graph()
        assert issues == [], f"deep-qa workflow has issues: {issues}"

    def test_name(self) -> None:
        wf = self._get_wf()
        assert wf.name == "deep-qa"

    def test_start_node(self) -> None:
        wf = self._get_wf()
        assert wf.start_node == "health_checker"

    def test_has_expected_nodes(self) -> None:
        wf = self._get_wf()
        assert set(wf.nodes.keys()) == {
            "health_checker", "code_reviewer", "gate_review",
            "adversarial_tester",
            "gate_precheck", "post_review",
        }

    def test_specialist_roles(self) -> None:
        wf = self._get_wf()
        node_roles = {
            "health_checker": AgentRole.HEALTH_CHECKER,
            "code_reviewer": AgentRole.CODE_REVIEWER,
            "adversarial_tester": AgentRole.ADVERSARIAL_TESTER,
        }
        for nid, expected_role in node_roles.items():
            node = wf.nodes[nid]
            assert isinstance(node, AgentNode)
            assert node.role == expected_role

    def test_specialist_reads_cleared(self) -> None:
        wf = self._get_wf()
        for nid in ("health_checker", "code_reviewer", "adversarial_tester"):
            node = wf.nodes[nid]
            assert isinstance(node, AgentNode)
            assert node.reads == set()

    def test_post_review_node(self) -> None:
        wf = self._get_wf()
        post = wf.nodes["post_review"]
        assert isinstance(post, FnNode)
        assert "factory review" in post.command
        assert "$VERDICT" in post.command
        assert "$PR_NUMBER" in post.command

    def test_no_builder_node(self) -> None:
        wf = self._get_wf()
        assert "builder" not in wf.nodes

    def test_no_reloop_edges(self) -> None:
        wf = self._get_wf()
        reloop = [e for e in wf.edges if e.condition == VerdictType.RELOOP]
        assert reloop == []

    def test_gate_review_is_fn(self) -> None:
        wf = self._get_wf()
        gate = wf.nodes["gate_review"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "fn"
        assert "CRITICAL_FOUND" in gate.evaluator_command

    def test_precheck_routes_to_post_review(self) -> None:
        wf = self._get_wf()
        from_precheck = [e for e in wf.edges if e.source == "gate_precheck"]
        assert len(from_precheck) == 2
        targets = {e.target for e in from_precheck}
        assert targets == {"post_review"}

    def test_trigger(self) -> None:
        from factory.models import ProjectState

        wf = self._get_wf()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "deep-qa"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})

    def test_registered(self) -> None:
        all_wf = register_all()
        assert "deep-qa" in all_wf

    def test_skill_export(self) -> None:
        from factory.workflow.skill_export import validate_skill, workflow_to_skill_md

        wf = self._get_wf()
        skill_md = workflow_to_skill_md(wf)
        issues = validate_skill(skill_md)
        assert issues == [], f"deep-qa skill has issues: {issues}"
        assert "workflow-deep-qa" in skill_md


# ── CLI parser accepts --mode deep-qa ────────────────────────────


class TestCliDeepQaMode:
    def test_parser_accepts_mode_deep_qa(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "factory.cli", "ceo", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert "deep-qa" in result.stdout

    def test_parser_accepts_mode_deep_qa_with_pr(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "factory.cli", "ceo", ".", "--mode", "deep-qa", "--pr", "42", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
