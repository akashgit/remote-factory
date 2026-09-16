"""Tests for the portable optimize workflow (.factory/workflows/optimize.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    VerdictType,
    Workflow,
)

# ── Fixture: load the portable workflow file ───────────────────


@pytest.fixture()
def optimize_workflow() -> Workflow:
    """Load the optimize workflow from .factory/workflows/optimize.py."""
    wf_path = Path(__file__).resolve().parent.parent / ".factory" / "workflows" / "optimize.py"
    assert wf_path.exists(), f"Workflow file not found: {wf_path}"

    spec = importlib.util.spec_from_file_location("_test_optimize_wf", wf_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        wf_fn = getattr(module, "workflow")
        return wf_fn()
    finally:
        sys.modules.pop(spec.name, None)


# ── Test 1: Graph Validation ──────────────────────────────────


class TestGraphValidation:
    def test_optimize_workflow_validates(self, optimize_workflow: Workflow) -> None:
        issues = optimize_workflow.validate_graph()
        assert issues == [], f"Validation issues: {issues}"

    def test_no_orphan_nodes(self, optimize_workflow: Workflow) -> None:
        node_ids = set(optimize_workflow.nodes.keys())
        referenced = {optimize_workflow.start_node}
        for edge in optimize_workflow.edges:
            referenced.add(edge.source)
            referenced.add(edge.target)
        for node in optimize_workflow.nodes.values():
            if isinstance(node, ForkNode):
                referenced.update(node.targets)
            if isinstance(node, JoinNode):
                referenced.update(node.sources)
        orphans = node_ids - referenced
        assert orphans == set(), f"Orphan nodes: {orphans}"


# ── Test 2: Trigger Function ─────────────────────────────────


class TestTrigger:
    def test_trigger_exists(self, optimize_workflow: Workflow) -> None:
        assert optimize_workflow.trigger is not None

    def test_trigger_activates(self, optimize_workflow: Workflow) -> None:
        assert optimize_workflow.trigger(
            ProjectState.HAS_FACTORY, {"mode": "optimize", "focus": "vector_hmc_step!"}
        )

    def test_trigger_no_focus(self, optimize_workflow: Workflow) -> None:
        assert not optimize_workflow.trigger(ProjectState.HAS_FACTORY, {"mode": "optimize"})

    def test_trigger_empty_focus(self, optimize_workflow: Workflow) -> None:
        assert not optimize_workflow.trigger(
            ProjectState.HAS_FACTORY, {"mode": "optimize", "focus": ""}
        )

    def test_trigger_none_focus(self, optimize_workflow: Workflow) -> None:
        assert not optimize_workflow.trigger(
            ProjectState.HAS_FACTORY, {"mode": "optimize", "focus": None}
        )

    def test_trigger_wrong_mode(self, optimize_workflow: Workflow) -> None:
        assert not optimize_workflow.trigger(
            ProjectState.HAS_FACTORY, {"mode": "improve", "focus": "vector_hmc_step!"}
        )


# ── Test 3: RELOOP Edge ──────────────────────────────────────


class TestReloopEdge:
    def test_qa_reloop_targets_builder(self, optimize_workflow: Workflow) -> None:
        reloop_edges = [
            e
            for e in optimize_workflow.edges
            if e.source == "gate_qa" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1
        assert reloop_edges[0].target == "builder"

    def test_build_gate_reloop_targets_builder(self, optimize_workflow: Workflow) -> None:
        reloop_edges = [
            e
            for e in optimize_workflow.edges
            if e.source == "gate_build" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1
        assert reloop_edges[0].target == "builder"

    def test_research_gate_reloop_targets_fork(self, optimize_workflow: Workflow) -> None:
        reloop_edges = [
            e
            for e in optimize_workflow.edges
            if e.source == "gate_research" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1
        assert reloop_edges[0].target == "fork_research"


# ── Test 4: Node Count and Types ─────────────────────────────


class TestNodeTypes:
    def test_node_count(self, optimize_workflow: Workflow) -> None:
        assert len(optimize_workflow.nodes) == 19

    def test_precondition_check_is_fn(self, optimize_workflow: Workflow) -> None:
        assert isinstance(optimize_workflow.nodes["precondition_check"], FnNode)

    def test_fork_research_is_fork(self, optimize_workflow: Workflow) -> None:
        assert isinstance(optimize_workflow.nodes["fork_research"], ForkNode)

    def test_researchers_are_agent_nodes(self, optimize_workflow: Workflow) -> None:
        for name in ("researcher_semantics", "researcher_conventions", "researcher_julia_perf"):
            node = optimize_workflow.nodes[name]
            assert isinstance(node, AgentNode)
            assert node.role == AgentRole.RESEARCHER

    def test_gate_strategy_is_user(self, optimize_workflow: Workflow) -> None:
        gate = optimize_workflow.nodes["gate_strategy"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "user"

    def test_builder_max_iterations(self, optimize_workflow: Workflow) -> None:
        builder = optimize_workflow.nodes["builder"]
        assert isinstance(builder, AgentNode)
        assert builder.max_iterations == 3

    def test_archivist_is_async(self, optimize_workflow: Workflow) -> None:
        archivist = optimize_workflow.nodes["archivist"]
        assert isinstance(archivist, AgentNode)
        assert archivist.blocking is False

    def test_gate_qa_is_fn_type(self, optimize_workflow: Workflow) -> None:
        gate = optimize_workflow.nodes["gate_qa"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_type == "fn"

    def test_workflow_is_terminal(self, optimize_workflow: Workflow) -> None:
        assert optimize_workflow.terminal is True

    def test_qa_statistical_is_fn(self, optimize_workflow: Workflow) -> None:
        assert isinstance(optimize_workflow.nodes["qa_statistical"], FnNode)

    def test_qa_statistical_writes(self, optimize_workflow: Workflow) -> None:
        node = optimize_workflow.nodes["qa_statistical"]
        assert isinstance(node, FnNode)
        assert ".factory/reviews/qa-statistical.md" in node.writes


# ── Test 5: Fork/Join Consistency ────────────────────────────


class TestForkJoinConsistency:
    def test_research_fork_join(self, optimize_workflow: Workflow) -> None:
        fork = optimize_workflow.nodes["fork_research"]
        join = optimize_workflow.nodes["join_research"]
        assert isinstance(fork, ForkNode)
        assert isinstance(join, JoinNode)
        assert set(fork.targets) == set(join.sources)

    def test_qa_fork_join(self, optimize_workflow: Workflow) -> None:
        fork = optimize_workflow.nodes["fork_qa"]
        join = optimize_workflow.nodes["join_qa"]
        assert isinstance(fork, ForkNode)
        assert isinstance(join, JoinNode)
        assert set(fork.targets) == set(join.sources)

    def test_qa_fork_has_four_targets(self, optimize_workflow: Workflow) -> None:
        fork = optimize_workflow.nodes["fork_qa"]
        assert isinstance(fork, ForkNode)
        assert set(fork.targets) == {
            "qa_conformance", "qa_tests", "qa_benchmark", "qa_statistical"
        }


# ── Test 6: Sacred Constraints ───────────────────────────────


class TestSacredConstraints:
    def test_builder_prompt_forbids_reference_modification(
        self, optimize_workflow: Workflow
    ) -> None:
        builder = optimize_workflow.nodes["builder"]
        assert isinstance(builder, AgentNode)
        assert "NEVER modify Reference" in builder.prompt_template

    def test_builder_prompt_requires_signature_match(
        self, optimize_workflow: Workflow
    ) -> None:
        builder = optimize_workflow.nodes["builder"]
        assert isinstance(builder, AgentNode)
        assert "signature MUST match" in builder.prompt_template

    def test_builder_prompt_has_conformance_tier(self, optimize_workflow: Workflow) -> None:
        builder = optimize_workflow.nodes["builder"]
        assert isinstance(builder, AgentNode)
        assert "{conformance_tier}" in builder.prompt_template

    def test_strategist_prompt_has_tier_constraints(self, optimize_workflow: Workflow) -> None:
        strategist = optimize_workflow.nodes["strategist"]
        assert isinstance(strategist, AgentNode)
        assert "bit-exact" in strategist.prompt_template
        assert "numerical" in strategist.prompt_template


# ── Test 7: Conformance Tier Variables ───────────────────────


class TestConformanceTier:
    def test_researcher_semantics_has_tier(self, optimize_workflow: Workflow) -> None:
        node = optimize_workflow.nodes["researcher_semantics"]
        assert isinstance(node, AgentNode)
        assert "{conformance_tier}" in node.prompt_template

    def test_researcher_conventions_has_tier(self, optimize_workflow: Workflow) -> None:
        node = optimize_workflow.nodes["researcher_conventions"]
        assert isinstance(node, AgentNode)
        assert "{conformance_tier}" in node.prompt_template

    def test_researcher_julia_perf_has_tier(self, optimize_workflow: Workflow) -> None:
        node = optimize_workflow.nodes["researcher_julia_perf"]
        assert isinstance(node, AgentNode)
        assert "{conformance_tier}" in node.prompt_template

    def test_qa_conformance_branches_on_tier(self, optimize_workflow: Workflow) -> None:
        node = optimize_workflow.nodes["qa_conformance"]
        assert isinstance(node, FnNode)
        assert "{conformance_tier}" in node.command
        assert "bit-exact" in node.command
        assert "{atol}" in node.command
        assert "{rtol}" in node.command

    def test_qa_conformance_has_using_evaluation(self, optimize_workflow: Workflow) -> None:
        node = optimize_workflow.nodes["qa_conformance"]
        assert isinstance(node, FnNode)
        assert "using Evaluation; " in node.command

    def test_qa_statistical_has_using_evaluation(self, optimize_workflow: Workflow) -> None:
        node = optimize_workflow.nodes["qa_statistical"]
        assert isinstance(node, FnNode)
        assert "using Evaluation; " in node.command

    def test_archivist_has_provenance(self, optimize_workflow: Workflow) -> None:
        node = optimize_workflow.nodes["archivist"]
        assert isinstance(node, AgentNode)
        assert "{conformance_tier}" in node.prompt_template
        assert "{atol}" in node.prompt_template
        assert "{rtol}" in node.prompt_template
        assert "ir_format_version" in node.prompt_template
        assert "commit_hash" in node.prompt_template
        assert "timestamp" in node.prompt_template

    def test_archivist_reads_statistical(self, optimize_workflow: Workflow) -> None:
        node = optimize_workflow.nodes["archivist"]
        assert isinstance(node, AgentNode)
        assert ".factory/reviews/qa-statistical.md" in node.reads

    def test_gate_qa_checks_statistical(self, optimize_workflow: Workflow) -> None:
        node = optimize_workflow.nodes["gate_qa"]
        assert isinstance(node, GateNode)
        assert node.evaluator_command is not None
        assert "qa-statistical.md" in node.evaluator_command
        assert ".factory/reviews/qa-statistical.md" in node.reads


# ── Test 8: Edge Count ───────────────────────────────────────


class TestEdges:
    def test_total_edge_count(self, optimize_workflow: Workflow) -> None:
        assert len(optimize_workflow.edges) == 26

    def test_qa_statistical_edges_exist(self, optimize_workflow: Workflow) -> None:
        fork_to_stat = [
            e for e in optimize_workflow.edges
            if e.source == "fork_qa" and e.target == "qa_statistical"
        ]
        stat_to_join = [
            e for e in optimize_workflow.edges
            if e.source == "qa_statistical" and e.target == "join_qa"
        ]
        assert len(fork_to_stat) == 1
        assert len(stat_to_join) == 1


# ── Test 9: Scope Mode ──────────────────────────────────────


class TestScopeMode:
    def test_precondition_detects_scope_prefix(self, optimize_workflow: Workflow) -> None:
        node = optimize_workflow.nodes["precondition_check"]
        assert isinstance(node, FnNode)
        assert "scope:" in node.command
        assert "SCOPE_MODE" in node.command

    def test_precondition_notes_document_scope(self, optimize_workflow: Workflow) -> None:
        node = optimize_workflow.nodes["precondition_check"]
        assert isinstance(node, FnNode)
        assert "scope:" in node.notes
