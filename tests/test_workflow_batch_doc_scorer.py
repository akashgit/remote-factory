"""Tests for the batch-document-scorer portable workflow.

Covers:
1. Graph validation — the workflow DAG is structurally sound
2. Skill export — the workflow produces valid SKILL.md content
3. Trigger function — fires only for mode='batch-document-scorer'
4. Node inventory — all expected nodes exist with correct types
5. Edge inventory — all expected edges exist with correct conditions
6. Data flow — reads/writes are consistent across the pipeline
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

# Import the portable workflow module directly from its file path
_WORKFLOW_DIR = Path(__file__).resolve().parent.parent / ".factory" / "workflows"


@pytest.fixture()
def batch_doc_scorer_module():
    """Import batch_doc_scorer.py as a module."""
    module_path = _WORKFLOW_DIR / "batch_doc_scorer.py"
    if not module_path.exists():
        pytest.skip(f"Workflow file not found: {module_path}")
    spec = importlib.util.spec_from_file_location("batch_doc_scorer", module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def wf(batch_doc_scorer_module):
    """Build the workflow instance."""
    return batch_doc_scorer_module.workflow()


# ── Meta dict ────────────────────────────────────────────────────


class TestMeta:
    def test_meta_has_required_keys(self, batch_doc_scorer_module) -> None:
        meta = batch_doc_scorer_module.meta
        assert "name" in meta
        assert "description" in meta
        assert meta["name"] == "batch-document-scorer"

    def test_meta_description_nonempty(self, batch_doc_scorer_module) -> None:
        assert len(batch_doc_scorer_module.meta["description"]) > 20


# ── Graph validation ─────────────────────────────────────────────


class TestGraphValidation:
    def test_workflow_validates(self, wf) -> None:
        """The workflow DAG must pass structural validation (no cycles,
        all nodes reachable, fork/join consistency, etc.)."""
        issues = wf.validate_graph()
        assert not issues, f"Graph validation failed: {issues}"

    def test_workflow_name(self, wf) -> None:
        assert wf.name == "batch-document-scorer"

    def test_workflow_is_terminal(self, wf) -> None:
        assert wf.terminal is True

    def test_start_node(self, wf) -> None:
        assert wf.start_node == "scan_files"


# ── Node inventory ───────────────────────────────────────────────


class TestNodeInventory:
    EXPECTED_NODES = {
        "scan_files",
        "validate_scan",
        "score_files",
        "aggregate_report",
        "review_gate",
    }

    def test_all_expected_nodes_exist(self, wf) -> None:
        assert set(wf.nodes.keys()) == self.EXPECTED_NODES

    def test_scan_files_is_fn_node(self, wf) -> None:
        from factory.workflow.primitives import FnNode
        node = wf.nodes["scan_files"]
        assert isinstance(node, FnNode)
        assert node.command  # non-empty command

    def test_validate_scan_is_gate_node(self, wf) -> None:
        from factory.workflow.primitives import GateNode
        node = wf.nodes["validate_scan"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "fn"
        assert node.evaluator_command  # non-empty

    def test_score_files_is_agent_node(self, wf) -> None:
        from factory.workflow.primitives import AgentNode, AgentRole
        node = wf.nodes["score_files"]
        assert isinstance(node, AgentNode)
        assert node.role == AgentRole.RESEARCHER

    def test_aggregate_report_is_fn_node(self, wf) -> None:
        from factory.workflow.primitives import FnNode
        node = wf.nodes["aggregate_report"]
        assert isinstance(node, FnNode)
        assert node.command  # non-empty

    def test_review_gate_is_user_gate(self, wf) -> None:
        from factory.workflow.primitives import GateNode
        node = wf.nodes["review_gate"]
        assert isinstance(node, GateNode)
        assert node.evaluator_type == "user"


# ── Edge inventory ───────────────────────────────────────────────


class TestEdgeInventory:
    def test_edge_count(self, wf) -> None:
        assert len(wf.edges) == 5

    def test_scan_to_validate_edge(self, wf) -> None:
        edges = [(e.source, e.target, e.condition) for e in wf.edges]
        assert ("scan_files", "validate_scan", None) in edges

    def test_validate_proceed_to_score(self, wf) -> None:
        from factory.workflow.primitives import VerdictType
        edges = [(e.source, e.target, e.condition) for e in wf.edges]
        assert ("validate_scan", "score_files", VerdictType.PROCEED) in edges

    def test_validate_halt_to_review(self, wf) -> None:
        from factory.workflow.primitives import VerdictType
        edges = [(e.source, e.target, e.condition) for e in wf.edges]
        assert ("validate_scan", "review_gate", VerdictType.HALT) in edges

    def test_score_to_aggregate_edge(self, wf) -> None:
        edges = [(e.source, e.target, e.condition) for e in wf.edges]
        assert ("score_files", "aggregate_report", None) in edges

    def test_aggregate_to_review_edge(self, wf) -> None:
        edges = [(e.source, e.target, e.condition) for e in wf.edges]
        assert ("aggregate_report", "review_gate", None) in edges


# ── Data flow ────────────────────────────────────────────────────


class TestDataFlow:
    def test_scan_writes_files_txt(self, wf) -> None:
        node = wf.nodes["scan_files"]
        assert ".factory/workspace/doc-scorer/files.txt" in node.writes

    def test_validate_reads_files_txt(self, wf) -> None:
        node = wf.nodes["validate_scan"]
        assert ".factory/workspace/doc-scorer/files.txt" in node.reads

    def test_score_reads_files_txt(self, wf) -> None:
        node = wf.nodes["score_files"]
        assert ".factory/workspace/doc-scorer/files.txt" in node.reads

    def test_score_writes_scores_json(self, wf) -> None:
        node = wf.nodes["score_files"]
        assert ".factory/workspace/doc-scorer/scores.json" in node.writes

    def test_aggregate_reads_scores_json(self, wf) -> None:
        node = wf.nodes["aggregate_report"]
        assert ".factory/workspace/doc-scorer/scores.json" in node.reads

    def test_aggregate_writes_both_reports(self, wf) -> None:
        node = wf.nodes["aggregate_report"]
        assert ".factory/reports/doc-quality-report.md" in node.writes
        assert ".factory/reports/doc-quality-report.json" in node.writes

    def test_review_reads_both_reports(self, wf) -> None:
        node = wf.nodes["review_gate"]
        assert ".factory/reports/doc-quality-report.md" in node.reads
        assert ".factory/reports/doc-quality-report.json" in node.reads


# ── Skill export ─────────────────────────────────────────────────


class TestSkillExport:
    def test_skill_export_produces_valid_skill(self, wf) -> None:
        from factory.workflow.skill_export import validate_skill, workflow_to_skill_md

        skill_md = workflow_to_skill_md(wf)
        issues = validate_skill(skill_md)
        assert issues == [], f"Skill export issues: {issues}"

    def test_skill_contains_workflow_name(self, wf) -> None:
        from factory.workflow.skill_export import workflow_to_skill_md

        skill_md = workflow_to_skill_md(wf)
        assert "batch-document-scorer" in skill_md


# ── Trigger function ─────────────────────────────────────────────


class TestTrigger:
    def test_trigger_matches_correct_mode(self, wf) -> None:
        from factory.workflow.primitives import ProjectState

        assert wf.trigger is not None
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "batch-document-scorer"}) is True

    def test_trigger_rejects_wrong_mode(self, wf) -> None:
        from factory.workflow.primitives import ProjectState

        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "pr-review"}) is False
        assert wf.trigger(ProjectState.HAS_FACTORY, {"mode": "build"}) is False

    def test_trigger_rejects_no_mode(self, wf) -> None:
        from factory.workflow.primitives import ProjectState

        assert wf.trigger(ProjectState.HAS_FACTORY, {}) is False

    def test_trigger_works_with_any_project_state(self, wf) -> None:
        from factory.workflow.primitives import ProjectState

        ctx: dict[str, Any] = {"mode": "batch-document-scorer"}
        for state in ProjectState:
            assert wf.trigger(state, ctx) is True


# ── Score files agent node details ───────────────────────────────


class TestScoreFilesAgent:
    def test_score_files_has_post_checks(self, wf) -> None:
        node = wf.nodes["score_files"]
        assert len(node.post_checks) > 0

    def test_post_check_validates_scores_json(self, wf) -> None:
        node = wf.nodes["score_files"]
        check = node.post_checks[0]
        assert check.must_exist is True
        assert check.min_size > 0
        assert '"files"' in check.must_contain
        assert '"grammar"' in check.must_contain
        assert '"readability"' in check.must_contain
        assert '"structure"' in check.must_contain

    def test_score_files_timeout(self, wf) -> None:
        node = wf.nodes["score_files"]
        assert node.timeout is not None
        assert node.timeout >= 600  # at least 10 minutes for large file sets
