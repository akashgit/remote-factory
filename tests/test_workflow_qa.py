"""Tests for QA mode: Workflow.subgraph(), qa_workflow() structure, CLI parser."""

from __future__ import annotations

import subprocess
import sys

import pytest

from factory.workflow.definitions import improve_workflow, qa_workflow, register_all
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
        sub = wf.subgraph({"qa", "gate_qa"}, name="test", start_node="qa")
        assert set(sub.nodes.keys()) == {"qa", "gate_qa"}

    def test_filters_edges(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"qa", "gate_qa"}, name="test", start_node="qa")
        for edge in sub.edges:
            assert edge.source in sub.nodes
            assert edge.target in sub.nodes

    def test_deep_copies_nodes(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"qa", "gate_qa"}, name="test", start_node="qa")
        assert sub.nodes["qa"] is not wf.nodes["qa"]

    def test_sets_name_and_start_node(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"qa", "gate_qa"}, name="myname", start_node="qa")
        assert sub.name == "myname"
        assert sub.start_node == "qa"

    def test_missing_node_raises(self) -> None:
        wf = improve_workflow()
        with pytest.raises(ValueError, match="node 'nonexistent'"):
            wf.subgraph({"nonexistent"}, name="test", start_node="nonexistent")

    def test_preserves_edge_between_included_nodes(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph(
            {"qa", "gate_qa", "gate_precheck"}, name="test", start_node="qa",
        )
        edge_pairs = {(e.source, e.target) for e in sub.edges}
        assert ("qa", "gate_qa") in edge_pairs
        assert ("gate_qa", "gate_precheck") in edge_pairs

    def test_excludes_edges_to_outside_nodes(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"qa", "gate_qa"}, name="test", start_node="qa")
        for edge in sub.edges:
            assert edge.target != "builder"
            assert edge.target != "gate_precheck"


# ── qa_workflow() structure ─────────────────────────────────────


class TestQaWorkflow:
    def test_valid_graph(self) -> None:
        wf = qa_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"qa workflow has issues: {issues}"

    def test_name(self) -> None:
        wf = qa_workflow()
        assert wf.name == "qa"

    def test_start_node(self) -> None:
        wf = qa_workflow()
        assert wf.start_node == "qa"

    def test_has_expected_nodes(self) -> None:
        wf = qa_workflow()
        assert set(wf.nodes.keys()) == {"qa", "gate_qa", "gate_precheck", "post_review"}

    def test_qa_node_from_improve(self) -> None:
        wf = qa_workflow()
        qa_node = wf.nodes["qa"]
        assert isinstance(qa_node, AgentNode)
        assert qa_node.role == AgentRole.QA

    def test_gate_qa_no_builder_reference(self) -> None:
        wf = qa_workflow()
        gate = wf.nodes["gate_qa"]
        assert isinstance(gate, GateNode)
        assert "RELOOP" not in gate.gate_prompt
        assert "builder" not in gate.gate_prompt.lower()
        assert "HALT" in gate.gate_prompt

        # Prompt is derived from improve's gate_qa — first sentence must match.
        improve_gate = improve_workflow().nodes["gate_qa"]
        assert isinstance(improve_gate, GateNode)
        first_sentence = improve_gate.gate_prompt.split(". ")[0] + "."
        assert gate.gate_prompt.startswith(first_sentence)

    def test_post_review_node(self) -> None:
        wf = qa_workflow()
        post = wf.nodes["post_review"]
        assert isinstance(post, FnNode)
        assert "factory review" in post.command
        assert "$VERDICT" in post.command
        assert "$PR_NUMBER" in post.command

    def test_no_builder_node(self) -> None:
        wf = qa_workflow()
        assert "builder" not in wf.nodes

    def test_no_reloop_edges(self) -> None:
        wf = qa_workflow()
        reloop = [e for e in wf.edges if e.condition == VerdictType.RELOOP]
        assert reloop == []

    def test_gate_qa_halt_goes_to_post_review(self) -> None:
        wf = qa_workflow()
        halt_edges = [
            e for e in wf.edges
            if e.source == "gate_qa" and e.condition == VerdictType.HALT
        ]
        assert len(halt_edges) == 1
        assert halt_edges[0].target == "post_review"

    def test_precheck_routes_to_post_review(self) -> None:
        wf = qa_workflow()
        from_precheck = [e for e in wf.edges if e.source == "gate_precheck"]
        assert len(from_precheck) == 2
        targets = {e.target for e in from_precheck}
        assert targets == {"post_review"}

    def test_trigger(self) -> None:
        from factory.models import ProjectState

        wf = qa_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "qa"})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {})
        assert not wf.trigger(ProjectState.HAS_FACTORY, {"mode": "improve"})

    def test_registered(self) -> None:
        all_wf = register_all()
        assert "qa" in all_wf

    def test_skill_export(self) -> None:
        from factory.workflow.skill_export import validate_skill, workflow_to_skill_md

        wf = qa_workflow()
        skill_md = workflow_to_skill_md(wf)
        issues = validate_skill(skill_md)
        assert issues == [], f"qa skill has issues: {issues}"
        assert "workflow-qa" in skill_md


# ── CLI parser accepts --mode qa ────────────────────────────────


class TestCliQaMode:
    def test_parser_accepts_mode_qa(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "factory.cli", "ceo", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert "qa" in result.stdout

    def test_parser_accepts_mode_qa_with_pr(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "factory.cli", "ceo", ".", "--mode", "qa", "--pr", "42", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
