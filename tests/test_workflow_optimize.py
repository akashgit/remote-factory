"""Tests for the optimize workflow graph definition."""

from __future__ import annotations

from collections import defaultdict

import pytest

from factory.workflow.definitions import optimize_workflow
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    FnNode,
    GateNode,
    VerdictType,
)


class TestOptimizeWorkflowStructure:
    """Verify the optimize workflow graph has the correct structure."""

    def test_has_7_nodes(self) -> None:
        wf = optimize_workflow()
        assert len(wf.nodes) == 7

    def test_node_ids(self) -> None:
        wf = optimize_workflow()
        expected = {"baseline", "gate_baseline", "mutate", "apply", "execute", "gate_improve", "test_eval"}
        assert set(wf.nodes.keys()) == expected

    def test_node_types(self) -> None:
        wf = optimize_workflow()
        assert isinstance(wf.nodes["baseline"], FnNode)
        assert isinstance(wf.nodes["gate_baseline"], GateNode)
        assert isinstance(wf.nodes["mutate"], AgentNode)
        assert isinstance(wf.nodes["apply"], FnNode)
        assert isinstance(wf.nodes["execute"], FnNode)
        assert isinstance(wf.nodes["gate_improve"], GateNode)
        assert isinstance(wf.nodes["test_eval"], FnNode)

    def test_mutate_is_strategist(self) -> None:
        wf = optimize_workflow()
        assert wf.nodes["mutate"].role == AgentRole.STRATEGIST

    def test_start_node(self) -> None:
        wf = optimize_workflow()
        assert wf.start_node == "baseline"

    def test_is_terminal(self) -> None:
        wf = optimize_workflow()
        assert wf.terminal is True

    def test_name(self) -> None:
        wf = optimize_workflow()
        assert wf.name == "optimize"


class TestOptimizeWorkflowEdges:
    """Verify edge connectivity."""

    def test_edge_count(self) -> None:
        wf = optimize_workflow()
        assert len(wf.edges) == 8

    def test_baseline_to_gate_baseline(self) -> None:
        wf = optimize_workflow()
        edge = next(e for e in wf.edges if e.source == "baseline")
        assert edge.target == "gate_baseline"
        assert edge.condition is None

    def test_gate_baseline_proceed_to_mutate(self) -> None:
        wf = optimize_workflow()
        edge = next(
            e for e in wf.edges
            if e.source == "gate_baseline" and e.condition == VerdictType.PROCEED
        )
        assert edge.target == "mutate"

    def test_gate_baseline_halt_to_test_eval(self) -> None:
        wf = optimize_workflow()
        edge = next(
            e for e in wf.edges
            if e.source == "gate_baseline" and e.condition == VerdictType.HALT
        )
        assert edge.target == "test_eval"

    def test_mutate_to_apply(self) -> None:
        wf = optimize_workflow()
        edge = next(e for e in wf.edges if e.source == "mutate")
        assert edge.target == "apply"

    def test_apply_to_execute(self) -> None:
        wf = optimize_workflow()
        edge = next(e for e in wf.edges if e.source == "apply")
        assert edge.target == "execute"

    def test_execute_to_gate_improve(self) -> None:
        wf = optimize_workflow()
        edge = next(e for e in wf.edges if e.source == "execute")
        assert edge.target == "gate_improve"

    def test_gate_improve_proceed_to_test_eval(self) -> None:
        wf = optimize_workflow()
        edge = next(
            e for e in wf.edges
            if e.source == "gate_improve" and e.condition == VerdictType.PROCEED
        )
        assert edge.target == "test_eval"

    def test_gate_improve_reloop_to_mutate(self) -> None:
        wf = optimize_workflow()
        edge = next(
            e for e in wf.edges
            if e.source == "gate_improve" and e.condition == VerdictType.RELOOP
        )
        assert edge.target == "mutate"


class TestOptimizeWorkflowValidation:
    """Graph validation should pass."""

    def test_validates_clean(self) -> None:
        wf = optimize_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"optimize workflow has issues: {issues}"


class TestOptimizeWorkflowRegistry:
    """Verify optimize is in the builtin registry."""

    def test_in_registry(self) -> None:
        from factory.workflow.definitions import _get_builtin_registry
        registry = _get_builtin_registry()
        assert "optimize" in registry

    def test_registry_callable_returns_workflow(self) -> None:
        from factory.workflow.definitions import _get_builtin_registry
        registry = _get_builtin_registry()
        wf = registry["optimize"]()
        assert wf.name == "optimize"
        assert len(wf.nodes) == 7


class TestOptimizeWorkflowReadsWrites:
    """Verify node reads/writes are consistent for file-based inter-node communication."""

    def test_baseline_writes_baseline_json(self) -> None:
        wf = optimize_workflow()
        assert ".factory/optimization/baseline.json" in wf.nodes["baseline"].writes

    def test_mutate_reads_skill_and_state(self) -> None:
        wf = optimize_workflow()
        reads = wf.nodes["mutate"].reads
        assert ".factory/optimization/current_skill.md" in reads
        assert ".factory/optimization/state.json" in reads

    def test_mutate_writes_mutation(self) -> None:
        wf = optimize_workflow()
        assert ".factory/optimization/mutation.json" in wf.nodes["mutate"].writes

    def test_apply_reads_mutation(self) -> None:
        wf = optimize_workflow()
        assert ".factory/optimization/mutation.json" in wf.nodes["apply"].reads

    def test_apply_writes_skill(self) -> None:
        wf = optimize_workflow()
        assert ".factory/optimization/current_skill.md" in wf.nodes["apply"].writes

    def test_test_eval_writes_result(self) -> None:
        wf = optimize_workflow()
        assert ".factory/optimization/test_result.json" in wf.nodes["test_eval"].writes


class TestOptimizeWorkflowPaths:
    """Verify the two main paths through the graph."""

    def _build_adj(self, wf):
        adj = defaultdict(list)
        for e in wf.edges:
            adj[e.source].append((e.target, e.condition))
        return adj

    def test_baseline_halt_path(self) -> None:
        """baseline → gate_baseline → (HALT) → test_eval"""
        wf = optimize_workflow()
        adj = self._build_adj(wf)
        # baseline → gate_baseline
        targets = [t for t, c in adj["baseline"]]
        assert "gate_baseline" in targets
        # gate_baseline → test_eval on HALT
        halt_targets = [t for t, c in adj["gate_baseline"] if c == VerdictType.HALT]
        assert "test_eval" in halt_targets

    def test_mutation_reloop_proceed_path(self) -> None:
        """gate_baseline → (PROCEED) → mutate → apply → execute → gate_improve → (PROCEED) → test_eval"""
        wf = optimize_workflow()
        adj = self._build_adj(wf)
        proceed_from_baseline = [t for t, c in adj["gate_baseline"] if c == VerdictType.PROCEED]
        assert "mutate" in proceed_from_baseline

        targets_from_mutate = [t for t, c in adj["mutate"]]
        assert "apply" in targets_from_mutate

        targets_from_apply = [t for t, c in adj["apply"]]
        assert "execute" in targets_from_apply

        targets_from_execute = [t for t, c in adj["execute"]]
        assert "gate_improve" in targets_from_execute

        reloop_targets = [t for t, c in adj["gate_improve"] if c == VerdictType.RELOOP]
        assert "mutate" in reloop_targets

        proceed_targets = [t for t, c in adj["gate_improve"] if c == VerdictType.PROCEED]
        assert "test_eval" in proceed_targets
