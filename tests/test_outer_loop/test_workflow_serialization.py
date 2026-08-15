"""Tests for Workflow.to_dict() / from_dict() round-trip serialization."""

from __future__ import annotations

import pytest

from factory.workflow.definitions import register_all
from factory.workflow.primitives import Workflow


class TestWorkflowRoundTrip:
    """Verify that to_dict → from_dict preserves structural identity."""

    def test_simple_workflow(self, simple_workflow: Workflow) -> None:
        data = simple_workflow.to_dict()
        restored = Workflow.from_dict(data)

        assert restored.name == simple_workflow.name
        assert restored.start_node == simple_workflow.start_node
        assert restored.terminal == simple_workflow.terminal
        assert set(restored.nodes.keys()) == set(simple_workflow.nodes.keys())
        assert len(restored.edges) == len(simple_workflow.edges)

        for nid in simple_workflow.nodes:
            orig = simple_workflow.nodes[nid]
            rest = restored.nodes[nid]
            assert type(orig).__name__ == type(rest).__name__
            assert orig.id == rest.id

    def test_round_trip_preserves_node_types(self, simple_workflow: Workflow) -> None:
        data = simple_workflow.to_dict()
        restored = Workflow.from_dict(data)

        for nid, node_data in data["nodes"].items():
            assert "_type" in node_data
            restored_node = restored.nodes[nid]
            assert type(restored_node).__name__ == node_data["_type"]

    def test_round_trip_preserves_edges(self, simple_workflow: Workflow) -> None:
        data = simple_workflow.to_dict()
        restored = Workflow.from_dict(data)

        orig_edges = {(e.source, e.target, e.condition) for e in simple_workflow.edges}
        rest_edges = {(e.source, e.target, e.condition) for e in restored.edges}
        assert orig_edges == rest_edges

    def test_validates_after_round_trip(self, simple_workflow: Workflow) -> None:
        data = simple_workflow.to_dict()
        restored = Workflow.from_dict(data)
        issues = restored.validate_graph()
        assert issues == []

    def test_unknown_node_type_raises(self) -> None:
        data = {
            "name": "bad",
            "nodes": {"n1": {"_type": "UnknownNode", "id": "n1"}},
            "edges": [],
            "start_node": "n1",
        }
        with pytest.raises(ValueError, match="Unknown node type"):
            Workflow.from_dict(data)


class TestBuiltinWorkflowRoundTrips:
    """Round-trip all builtin workflows through to_dict/from_dict."""

    @pytest.fixture(scope="class")
    def all_workflows(self) -> dict[str, Workflow]:
        return register_all()

    def test_all_workflows_round_trip(self, all_workflows: dict[str, Workflow]) -> None:
        assert len(all_workflows) > 0
        for name, wf in all_workflows.items():
            data = wf.to_dict()
            restored = Workflow.from_dict(data)

            assert restored.name == wf.name, f"{name}: name mismatch"
            assert restored.start_node == wf.start_node, f"{name}: start_node mismatch"
            assert set(restored.nodes.keys()) == set(wf.nodes.keys()), (
                f"{name}: node set mismatch"
            )
            assert len(restored.edges) == len(wf.edges), f"{name}: edge count mismatch"

            for nid in wf.nodes:
                assert type(restored.nodes[nid]).__name__ == type(wf.nodes[nid]).__name__, (
                    f"{name}: node {nid} type mismatch"
                )

    def test_all_workflows_validate_after_round_trip(
        self, all_workflows: dict[str, Workflow]
    ) -> None:
        for name, wf in all_workflows.items():
            data = wf.to_dict()
            restored = Workflow.from_dict(data)
            issues = restored.validate_graph()
            orig_issues = wf.validate_graph()
            assert issues == orig_issues, (
                f"{name}: validation issues differ after round-trip: {issues} vs {orig_issues}"
            )
