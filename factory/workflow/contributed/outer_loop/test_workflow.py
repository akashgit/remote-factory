"""Tests for the outer-loop contributed workflow."""

from __future__ import annotations

from factory.workflow.contributed.outer_loop import meta, workflow
from factory.workflow.primitives import (
    FnNode,
    GateNode,
    VerdictType,
)


class TestOuterLoopWorkflow:
    """Tests for outer-loop workflow graph structure."""

    def test_workflow_name(self) -> None:
        wf = workflow()
        assert wf.name == "outer-loop"

    def test_meta_name(self) -> None:
        assert meta["name"] == "outer-loop"

    def test_node_count(self) -> None:
        wf = workflow()
        assert len(wf.nodes) == 6

    def test_required_nodes_present(self) -> None:
        wf = workflow()
        for name in ("seed", "evaluate", "reflect", "evolve", "gate_converge"):
            assert name in wf.nodes, f"Missing node: {name}"

    def test_seed_is_fn_node(self) -> None:
        wf = workflow()
        assert isinstance(wf.nodes["seed"], FnNode)

    def test_gate_converge_is_gate_node(self) -> None:
        wf = workflow()
        assert isinstance(wf.nodes["gate_converge"], GateNode)

    def test_start_node(self) -> None:
        wf = workflow()
        assert wf.start_node == "seed"

    def test_terminal(self) -> None:
        wf = workflow()
        assert wf.terminal is True

    def test_reloop_edge_exists(self) -> None:
        wf = workflow()
        reloop_edges = [
            e for e in wf.edges
            if e.source == "gate_converge" and e.target == "evaluate"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1

    def test_forward_chain(self) -> None:
        wf = workflow()
        expected_chain = [
            ("seed", "evaluate"),
            ("evaluate", "reflect"),
            ("reflect", "evolve"),
            ("evolve", "gate_converge"),
        ]
        for src, tgt in expected_chain:
            assert any(
                e.source == src and e.target == tgt for e in wf.edges
            ), f"Missing edge: {src} → {tgt}"
