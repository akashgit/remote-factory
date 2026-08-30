"""Tests for structural hashing, GED, feature extraction, and novelty filtering."""

from __future__ import annotations

from factory.outer_loop.similarity import (
    NoveltyFilter,
    compute_features,
    graph_edit_distance,
    structural_hash,
)
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    Workflow,
)


class TestStructuralHash:
    def test_deterministic(self, simple_workflow: Workflow) -> None:
        h1 = structural_hash(simple_workflow)
        h2 = structural_hash(simple_workflow)
        assert h1 == h2

    def test_different_workflows_different_hash(self, simple_workflow: Workflow) -> None:
        other = Workflow(
            name="other",
            nodes={"a": FnNode(id="a", command="echo a")},
            edges=[],
            start_node="a",
        )
        assert structural_hash(simple_workflow) != structural_hash(other)

    def test_same_structure_same_hash(self) -> None:
        nodes1 = {
            "a": FnNode(id="a", command="echo a"),
            "b": FnNode(id="b", command="echo b"),
        }
        edges1 = [Edge(source="a", target="b")]
        wf1 = Workflow(name="w", nodes=nodes1, edges=edges1, start_node="a")

        nodes2 = {
            "a": FnNode(id="a", command="echo a"),
            "b": FnNode(id="b", command="echo b"),
        }
        edges2 = [Edge(source="a", target="b")]
        wf2 = Workflow(name="w", nodes=nodes2, edges=edges2, start_node="a")

        assert structural_hash(wf1) == structural_hash(wf2)


class TestGraphEditDistance:
    def test_identical_workflows(self, simple_workflow: Workflow) -> None:
        assert graph_edit_distance(simple_workflow, simple_workflow) == 0

    def test_different_node_sets(self) -> None:
        wf1 = Workflow(
            name="w1",
            nodes={
                "a": FnNode(id="a", command="x"),
                "b": FnNode(id="b", command="x"),
            },
            edges=[Edge(source="a", target="b")],
            start_node="a",
        )
        wf2 = Workflow(
            name="w2",
            nodes={
                "a": FnNode(id="a", command="x"),
                "c": FnNode(id="c", command="x"),
            },
            edges=[Edge(source="a", target="c")],
            start_node="a",
        )
        dist = graph_edit_distance(wf1, wf2)
        assert dist >= 2

    def test_type_change_adds_distance(self) -> None:
        wf1 = Workflow(
            name="w",
            nodes={"a": FnNode(id="a", command="x")},
            edges=[],
            start_node="a",
        )
        wf2 = Workflow(
            name="w",
            nodes={"a": AgentNode(id="a", role=AgentRole.RESEARCHER)},
            edges=[],
            start_node="a",
        )
        assert graph_edit_distance(wf1, wf2) == 1


class TestComputeFeatures:
    def test_simple_workflow(self, simple_workflow: Workflow) -> None:
        features = compute_features(simple_workflow)
        depth, fork_degree, agent_count, gate_count = features[:4]
        assert depth >= 4
        assert fork_degree == 0
        assert agent_count == 3
        assert gate_count == 1
        assert len(features) > 4  # edge, param, and prompt features appended

    def test_workflow_with_fork(self) -> None:
        nodes = {
            "start": FnNode(id="start", command="x"),
            "fork": ForkNode(id="fork", targets=["a", "b", "c"]),
            "a": AgentNode(id="a", role=AgentRole.RESEARCHER),
            "b": AgentNode(id="b", role=AgentRole.BUILDER),
            "c": AgentNode(id="c", role=AgentRole.STRATEGIST),
            "join": JoinNode(id="join", sources=["a", "b", "c"]),
            "gate": GateNode(id="gate", evaluator_type="fn"),
        }
        edges = [
            Edge(source="start", target="fork"),
            Edge(source="fork", target="a"),
            Edge(source="fork", target="b"),
            Edge(source="fork", target="c"),
            Edge(source="a", target="join"),
            Edge(source="b", target="join"),
            Edge(source="c", target="join"),
            Edge(source="join", target="gate"),
        ]
        wf = Workflow(name="forked", nodes=nodes, edges=edges, start_node="start")
        features = compute_features(wf)
        depth, fork_degree, agent_count, gate_count = features[:4]
        assert fork_degree == 3
        assert agent_count == 3
        assert gate_count == 1
        assert len(features) > 4  # edge, param, and prompt features


class TestNoveltyFilter:
    def test_first_workflow_is_novel(self, simple_workflow: Workflow) -> None:
        nf = NoveltyFilter()
        assert nf.is_novel(simple_workflow) is True

    def test_duplicate_is_not_novel(self, simple_workflow: Workflow) -> None:
        nf = NoveltyFilter()
        nf.add(simple_workflow)
        assert nf.is_novel(simple_workflow) is False

    def test_similar_workflow_rejected_by_ged(self, simple_workflow: Workflow) -> None:
        nf = NoveltyFilter(min_edit_distance=2)
        nf.add(simple_workflow)

        other = Workflow(
            name=simple_workflow.name,
            nodes=dict(simple_workflow.nodes),
            edges=list(simple_workflow.edges),
            start_node=simple_workflow.start_node,
        )
        assert nf.is_novel(other) is False

    def test_very_different_workflow_is_novel(self, simple_workflow: Workflow) -> None:
        nf = NoveltyFilter(min_edit_distance=2)
        nf.add(simple_workflow)

        other = Workflow(
            name="totally_different",
            nodes={
                "x": FnNode(id="x", command="echo x"),
                "y": FnNode(id="y", command="echo y"),
                "z": FnNode(id="z", command="echo z"),
            },
            edges=[
                Edge(source="x", target="y"),
                Edge(source="y", target="z"),
            ],
            start_node="x",
        )
        assert nf.is_novel(other) is True

    def test_custom_threshold(self, simple_workflow: Workflow) -> None:
        nf = NoveltyFilter(min_edit_distance=100)
        nf.add(simple_workflow)
        other = Workflow(
            name="other",
            nodes={"a": FnNode(id="a", command="x")},
            edges=[],
            start_node="a",
        )
        assert nf.is_novel(other, threshold=1) is True
