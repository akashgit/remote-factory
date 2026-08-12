"""Tests for QA mode: parallel QA agents, qa_workflow() structure, CLI parser."""

from __future__ import annotations

import subprocess
import sys

import pytest

from factory.workflow.definitions import improve_workflow, qa_workflow, register_all
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    VerdictType,
)


# ── Workflow.subgraph() ─────────────────────────────────────────


class TestSubgraph:
    def test_extracts_requested_nodes(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"join_qa", "gate_qa"}, name="test", start_node="join_qa")
        assert set(sub.nodes.keys()) == {"join_qa", "gate_qa"}

    def test_filters_edges(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"join_qa", "gate_qa"}, name="test", start_node="join_qa")
        for edge in sub.edges:
            assert edge.source in sub.nodes
            assert edge.target in sub.nodes

    def test_deep_copies_nodes(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"join_qa", "gate_qa"}, name="test", start_node="join_qa")
        assert sub.nodes["join_qa"] is not wf.nodes["join_qa"]

    def test_sets_name_and_start_node(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"join_qa", "gate_qa"}, name="myname", start_node="join_qa")
        assert sub.name == "myname"
        assert sub.start_node == "join_qa"

    def test_missing_node_raises(self) -> None:
        wf = improve_workflow()
        with pytest.raises(ValueError, match="node 'nonexistent'"):
            wf.subgraph({"nonexistent"}, name="test", start_node="nonexistent")

    def test_preserves_edge_between_included_nodes(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph(
            {"join_qa", "gate_qa", "gate_precheck"}, name="test", start_node="join_qa",
        )
        edge_pairs = {(e.source, e.target) for e in sub.edges}
        assert ("join_qa", "gate_qa") in edge_pairs
        assert ("gate_qa", "gate_precheck") in edge_pairs

    def test_excludes_edges_to_outside_nodes(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"join_qa", "gate_qa"}, name="test", start_node="join_qa")
        for edge in sub.edges:
            assert edge.target != "builder"
            assert edge.target != "gate_precheck"


# ── Parallel QA in improve workflow ──────────────────────────────


class TestParallelQaInImprove:
    def test_has_fork_and_join(self) -> None:
        wf = improve_workflow()
        assert "fork_qa" in wf.nodes
        assert "join_qa" in wf.nodes
        assert isinstance(wf.nodes["fork_qa"], ForkNode)
        assert isinstance(wf.nodes["join_qa"], JoinNode)

    def test_fork_targets_three_qa_agents(self) -> None:
        wf = improve_workflow()
        fork = wf.nodes["fork_qa"]
        assert isinstance(fork, ForkNode)
        assert set(fork.targets) == {"qa_health", "qa_review", "qa_adversarial"}

    def test_join_sources_match_fork_targets(self) -> None:
        wf = improve_workflow()
        fork = wf.nodes["fork_qa"]
        join = wf.nodes["join_qa"]
        assert isinstance(fork, ForkNode)
        assert isinstance(join, JoinNode)
        assert set(join.sources) == set(fork.targets)

    def test_qa_agents_have_correct_roles(self) -> None:
        wf = improve_workflow()
        assert wf.nodes["qa_health"].role == AgentRole.QA_HEALTH  # type: ignore[union-attr]
        assert wf.nodes["qa_review"].role == AgentRole.QA_REVIEW  # type: ignore[union-attr]
        assert wf.nodes["qa_adversarial"].role == AgentRole.QA_ADVERSARIAL  # type: ignore[union-attr]

    def test_qa_agents_write_distinct_files(self) -> None:
        wf = improve_workflow()
        health_writes = wf.nodes["qa_health"].writes
        review_writes = wf.nodes["qa_review"].writes
        adversarial_writes = wf.nodes["qa_adversarial"].writes
        assert health_writes != review_writes != adversarial_writes

    def test_gate_qa_reads_all_three_outputs(self) -> None:
        wf = improve_workflow()
        gate = wf.nodes["gate_qa"]
        assert isinstance(gate, GateNode)
        assert ".factory/reviews/qa_health-latest.md" in gate.reads
        assert ".factory/reviews/qa_review-latest.md" in gate.reads
        assert ".factory/reviews/qa_adversarial-latest.md" in gate.reads

    def test_adversarial_reads_strategy(self) -> None:
        wf = improve_workflow()
        adv = wf.nodes["qa_adversarial"]
        assert isinstance(adv, AgentNode)
        assert ".factory/strategy/current.md" in adv.reads

    def test_review_reads_strategy(self) -> None:
        wf = improve_workflow()
        review = wf.nodes["qa_review"]
        assert isinstance(review, AgentNode)
        assert ".factory/strategy/current.md" in review.reads

    def test_edge_flow_build_gate_to_fork(self) -> None:
        wf = improve_workflow()
        edges = [e for e in wf.edges if e.source == "gate_build" and e.condition == VerdictType.PROCEED]
        assert len(edges) == 1
        assert edges[0].target == "fork_qa"

    def test_edge_flow_fork_to_join_to_gate(self) -> None:
        wf = improve_workflow()
        fork_edges = [e for e in wf.edges if e.source == "fork_qa"]
        assert any(e.target == "join_qa" for e in fork_edges)
        join_edges = [e for e in wf.edges if e.source == "join_qa"]
        assert any(e.target == "gate_qa" for e in join_edges)


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
        assert wf.start_node == "fork_qa"

    def test_has_expected_nodes(self) -> None:
        wf = qa_workflow()
        expected = {
            "fork_qa", "qa_health", "qa_review", "qa_adversarial",
            "join_qa", "gate_qa", "gate_precheck", "post_review",
        }
        assert set(wf.nodes.keys()) == expected

    def test_qa_agents_have_correct_roles(self) -> None:
        wf = qa_workflow()
        assert wf.nodes["qa_health"].role == AgentRole.QA_HEALTH  # type: ignore[union-attr]
        assert wf.nodes["qa_review"].role == AgentRole.QA_REVIEW  # type: ignore[union-attr]
        assert wf.nodes["qa_adversarial"].role == AgentRole.QA_ADVERSARIAL  # type: ignore[union-attr]

    def test_gate_qa_no_builder_reference(self) -> None:
        wf = qa_workflow()
        gate = wf.nodes["gate_qa"]
        assert isinstance(gate, GateNode)
        assert "RELOOP" not in gate.gate_prompt
        assert "builder" not in gate.gate_prompt.lower()
        assert "HALT" in gate.gate_prompt

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
