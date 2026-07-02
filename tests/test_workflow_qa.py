"""Tests for QA mode: Workflow.subgraph(), qa_workflow() structure, CLI parser, deep-qa contributed workflow."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from factory.workflow.definitions import improve_workflow, qa_workflow, register_all
from factory.workflow.executor import WorkflowExecutor
from factory.workflow.registry import WorkflowRegistry
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
        sub = wf.subgraph({"health_checker", "gate_health"}, name="test", start_node="health_checker")
        assert set(sub.nodes.keys()) == {"health_checker", "gate_health"}

    def test_filters_edges(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"health_checker", "gate_health"}, name="test", start_node="health_checker")
        for edge in sub.edges:
            assert edge.source in sub.nodes
            assert edge.target in sub.nodes

    def test_deep_copies_nodes(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"health_checker", "gate_health"}, name="test", start_node="health_checker")
        assert sub.nodes["health_checker"] is not wf.nodes["health_checker"]

    def test_sets_name_and_start_node(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"health_checker", "gate_health"}, name="myname", start_node="health_checker")
        assert sub.name == "myname"
        assert sub.start_node == "health_checker"

    def test_missing_node_raises(self) -> None:
        wf = improve_workflow()
        with pytest.raises(ValueError, match="node 'nonexistent'"):
            wf.subgraph({"nonexistent"}, name="test", start_node="nonexistent")

    def test_preserves_edge_between_included_nodes(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph(
            {"health_checker", "gate_health", "code_reviewer"}, name="test", start_node="health_checker",
        )
        edge_pairs = {(e.source, e.target) for e in sub.edges}
        assert ("health_checker", "gate_health") in edge_pairs
        assert ("gate_health", "code_reviewer") in edge_pairs

    def test_excludes_edges_to_outside_nodes(self) -> None:
        wf = improve_workflow()
        sub = wf.subgraph({"health_checker", "gate_health"}, name="test", start_node="health_checker")
        for edge in sub.edges:
            assert edge.target != "code_reviewer"
            assert edge.target != "builder"


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
        assert wf.start_node == "health_checker"

    def test_has_expected_nodes(self) -> None:
        wf = qa_workflow()
        assert set(wf.nodes.keys()) == {
            "health_checker", "gate_health", "code_reviewer", "gate_review",
            "adversarial_tester", "gate_adversarial", "join_verdict",
            "gate_qa", "gate_precheck", "post_review",
        }

    def test_specialist_nodes_from_improve(self) -> None:
        wf = qa_workflow()
        for nid in ("health_checker", "code_reviewer", "adversarial_tester"):
            node = wf.nodes[nid]
            assert isinstance(node, AgentNode)
            assert node.role == AgentRole.QA

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


# ── Deep-QA contributed workflow ───────────────────────────────


CONTRIB_WORKFLOW_SRC = Path(__file__).parent.parent / "workflows" / "deep_qa.py"


@pytest.fixture(autouse=True)
def _reset_wf_registry():
    """Reset WorkflowRegistry between tests so discovery is clean."""
    WorkflowRegistry.reset()
    yield
    WorkflowRegistry.reset()


class TestDeepQaContributedWorkflow:
    """Verify the deep-qa contributed workflow in workflows/deep_qa.py can be
    discovered via WorkflowRegistry and executed in dry-run mode."""

    @pytest.fixture
    def project_with_deep_qa(self, tmp_path: Path) -> Path:
        """Create a temp project with deep_qa.py in .factory/workflows/."""
        wf_dir = tmp_path / ".factory" / "workflows"
        wf_dir.mkdir(parents=True)
        (tmp_path / ".factory" / "reviews").mkdir()
        shutil.copy(CONTRIB_WORKFLOW_SRC, wf_dir / "deep_qa.py")
        return tmp_path

    def test_discovery_finds_deep_qa(self, project_with_deep_qa: Path) -> None:
        entries = WorkflowRegistry.discover(project_path=project_with_deep_qa)
        assert "deep-qa" in entries
        assert entries["deep-qa"].source == "project"

    def test_get_workflow_returns_valid_graph(self, project_with_deep_qa: Path) -> None:
        wf = WorkflowRegistry.get_workflow("deep-qa", project_with_deep_qa)
        assert wf is not None
        assert wf.name == "deep-qa"
        assert wf.start_node == "health_checker"
        issues = wf.validate_graph()
        assert issues == [], f"deep-qa graph issues: {issues}"

    def test_has_expected_nodes(self, project_with_deep_qa: Path) -> None:
        wf = WorkflowRegistry.get_workflow("deep-qa", project_with_deep_qa)
        assert wf is not None
        expected = {
            "health_checker", "gate_health", "code_reviewer", "gate_review",
            "adversarial_tester", "gate_adversarial", "join_verdict",
            "gate_precheck", "post_review",
        }
        assert expected.issubset(set(wf.nodes.keys()))

    async def test_dry_run_executes_all_nodes(self, project_with_deep_qa: Path) -> None:
        wf = WorkflowRegistry.get_workflow("deep-qa", project_with_deep_qa)
        assert wf is not None
        executor = WorkflowExecutor(wf, project_with_deep_qa, dry_run=True)
        result = await executor.execute()

        assert result.success
        assert not result.halted
        assert result.nodes_executed >= 7

    async def test_dry_run_node_sequence(self, project_with_deep_qa: Path) -> None:
        wf = WorkflowRegistry.get_workflow("deep-qa", project_with_deep_qa)
        assert wf is not None
        executor = WorkflowExecutor(wf, project_with_deep_qa, dry_run=True)
        result = await executor.execute()

        executed_nodes = [
            e["node_id"] for e in result.events
            if e["type"] == "node.started"
        ]
        assert "health_checker" in executed_nodes
        assert "code_reviewer" in executed_nodes
        assert "adversarial_tester" in executed_nodes
