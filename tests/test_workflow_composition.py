"""Tests for factory.workflow.composition — uses real workflows from definitions.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from factory.workflow.composition import (
    compose_serial,
    describe_nodes,
    find_terminal_nodes,
    prefix_nodes,
    trim_nodes,
    validate_composition,
)
from factory.workflow.definitions import (
    build_workflow,
    discover_workflow,
    improve_workflow,
    register_all,
    review_workflow,
)
from factory.workflow.primitives import (
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    VerdictType,
    Workflow,
)


# ── helpers ──────────────────────────────────────────────────────


def _linear_workflow(node_ids: list[str], name: str = "linear") -> Workflow:
    """Build a simple linear workflow of FnNodes for testing."""
    nodes: dict[str, FnNode] = {}
    edges: list[Edge] = []
    for nid in node_ids:
        nodes[nid] = FnNode(id=nid, command=f"echo {nid}")
    for i in range(len(node_ids) - 1):
        edges.append(Edge(source=node_ids[i], target=node_ids[i + 1]))
    return Workflow(
        name=name,
        nodes=nodes,
        edges=edges,
        start_node=node_ids[0],
        trigger=None,
    )


# ── 1. prefix_nodes ─────────────────────────────────────────────


def test_prefix_nodes_discover():
    """Prefix discover_workflow with 'd'. All IDs, edges, start_node should be prefixed."""
    wf = discover_workflow()
    prefixed = prefix_nodes(wf, "d")

    for nid in prefixed.nodes:
        assert nid.startswith("d_"), f"node '{nid}' missing prefix"
    for edge in prefixed.edges:
        assert edge.source.startswith("d_"), f"edge source '{edge.source}' missing prefix"
        assert edge.target.startswith("d_"), f"edge target '{edge.target}' missing prefix"
    assert prefixed.start_node == "d_discover"
    assert not prefixed.validate_graph()


def test_prefix_nodes_preserves_fork_join():
    """Prefix build_workflow (has ForkNode/JoinNode). Fork targets and Join sources are prefixed."""
    wf = build_workflow()
    prefixed = prefix_nodes(wf, "b")

    fork = None
    join = None
    for nid, node in prefixed.nodes.items():
        if isinstance(node, ForkNode):
            fork = node
        elif isinstance(node, JoinNode):
            join = node

    assert fork is not None, "build_workflow should have a ForkNode"
    assert join is not None, "build_workflow should have a JoinNode"

    for t in fork.targets:
        assert t.startswith("b_"), f"ForkNode target '{t}' missing prefix"
    for s in join.sources:
        assert s.startswith("b_"), f"JoinNode source '{s}' missing prefix"

    assert not prefixed.validate_graph()


# ── 2. find_terminal_nodes ───────────────────────────────────────


def test_find_terminal_nodes_discover():
    """discover_workflow terminal nodes should include 'redetect'."""
    wf = discover_workflow()
    terminals = find_terminal_nodes(wf)
    assert isinstance(terminals, list)
    assert "redetect" in terminals


# ── 3. compose_serial ────────────────────────────────────────────


def test_compose_serial_discover_review():
    """Chain discover → review. Bridge edge, all nodes present, valid graph."""
    d = discover_workflow()
    r = review_workflow()
    composed = compose_serial(
        d, r,
        end_node_w1="redetect",
        name="discover-then-review",
    )

    assert not composed.validate_graph()
    assert len(composed.nodes) == len(d.nodes) + len(r.nodes)

    # Bridge edge exists
    bridge = [
        e for e in composed.edges
        if e.source == "redetect" and e.target == r.start_node
    ]
    assert len(bridge) == 1
    assert composed.start_node == d.start_node


def test_compose_serial_id_conflict_raises():
    """Composing improve with itself should raise ValueError listing conflicts."""
    wf = improve_workflow()
    with pytest.raises(ValueError, match="ID conflicts"):
        compose_serial(
            wf, wf,
            end_node_w1="archivist",
            name="double-improve",
        )


def test_compose_serial_with_rename():
    """Compose discover with itself using rename dict. Both sets of nodes present."""
    d = discover_workflow()
    composed = compose_serial(
        d, d,
        end_node_w1="redetect",
        name="double-discover",
        rename={
            "discover": "discover2",
            "gate_discover": "gate_discover2",
            "redetect": "redetect2",
        },
    )

    assert not composed.validate_graph()
    assert "discover" in composed.nodes
    assert "discover2" in composed.nodes
    assert "redetect" in composed.nodes
    assert "redetect2" in composed.nodes
    assert len(composed.nodes) == len(d.nodes) * 2


def test_compose_serial_preserves_conditional_edges():
    """Conditional edges (e.g. RELOOP) in w1 survive composition."""
    d = discover_workflow()
    r = review_workflow()

    # discover has RELOOP edge: gate_discover → discover
    reloop_edges_before = [
        e for e in d.edges if e.condition == VerdictType.RELOOP
    ]
    assert len(reloop_edges_before) > 0

    composed = compose_serial(
        d, r,
        end_node_w1="redetect",
        name="discover-then-review",
    )

    reloop_edges_after = [
        e for e in composed.edges if e.condition == VerdictType.RELOOP
    ]
    assert len(reloop_edges_after) >= len(reloop_edges_before)


# ── 4. trim_nodes ────────────────────────────────────────────────


def test_trim_linear_fn_node():
    """Trim middle node from a 3-node linear chain."""
    wf = _linear_workflow(["a", "b", "c"])
    trimmed = trim_nodes(wf, {"b"})

    assert len(trimmed.nodes) == 2
    assert "a" in trimmed.nodes
    assert "c" in trimmed.nodes
    assert "b" not in trimmed.nodes

    bridge = [e for e in trimmed.edges if e.source == "a" and e.target == "c"]
    assert len(bridge) == 1
    assert not trimmed.validate_graph()


def test_trim_gate_node_raises():
    """Trimming a GateNode with conditional edges raises ValueError."""
    nodes: dict[str, FnNode | GateNode] = {
        "a": FnNode(id="a", command="echo a"),
        "gate": GateNode(
            id="gate",
            evaluator_type="fn",
            evaluator_command="echo PROCEED",
        ),
        "b": FnNode(id="b", command="echo b"),
        "c": FnNode(id="c", command="echo c"),
    }
    edges = [
        Edge(source="a", target="gate"),
        Edge(source="gate", target="b", condition=VerdictType.PROCEED),
        Edge(source="gate", target="a", condition=VerdictType.RELOOP),
    ]
    wf = Workflow(name="gate-test", nodes=nodes, edges=edges, start_node="a", trigger=None)

    with pytest.raises(ValueError, match="conditional edges"):
        trim_nodes(wf, {"gate"})


def test_trim_fork_node_raises():
    """Trimming a ForkNode raises ValueError about structural nodes."""
    wf = build_workflow()
    fork_ids = [nid for nid, n in wf.nodes.items() if isinstance(n, ForkNode)]
    assert fork_ids, "build_workflow should have a ForkNode"

    with pytest.raises(ValueError, match="structural nodes"):
        trim_nodes(wf, {fork_ids[0]})


def test_trim_data_flow_break_raises():
    """Trimming a node that writes a file read by a successor raises ValueError."""
    nodes: dict[str, FnNode] = {
        "a": FnNode(id="a", command="echo a"),
        "b": FnNode(id="b", command="echo b", writes={"output.md"}),
        "c": FnNode(id="c", command="echo c", reads={"output.md"}),
    }
    edges = [
        Edge(source="a", target="b"),
        Edge(source="b", target="c"),
    ]
    wf = Workflow(name="data-flow", nodes=nodes, edges=edges, start_node="a", trigger=None)

    with pytest.raises(ValueError, match="breaks data flow"):
        trim_nodes(wf, {"b"})


def test_trim_multiple_nodes():
    """Trim 2 nodes from a 5-node linear chain."""
    wf = _linear_workflow(["a", "b", "c", "d", "e"])
    trimmed = trim_nodes(wf, {"b", "d"})

    assert len(trimmed.nodes) == 3
    assert set(trimmed.nodes.keys()) == {"a", "c", "e"}

    # a → c and c → e should exist
    edges_by_src = {e.source: e.target for e in trimmed.edges}
    assert edges_by_src.get("a") == "c"
    assert edges_by_src.get("c") == "e"
    assert not trimmed.validate_graph()


# ── 5. validate_composition ──────────────────────────────────────


def test_validate_composition_clean():
    """validate_composition on a freshly composed workflow returns no issues."""
    d = discover_workflow()
    r = review_workflow()
    composed = compose_serial(
        d, r,
        end_node_w1="redetect",
        name="discover-then-review",
    )
    issues = validate_composition(composed)
    assert issues == []


def test_validate_composition_on_all_registered():
    """validate_composition on every registered workflow returns no issues."""
    all_wf = register_all()
    for name, wf in all_wf.items():
        issues = validate_composition(wf)
        assert issues == [], f"workflow '{name}' has composition issues: {issues}"


# ── 6. describe_nodes ───────────────────────────────────────────


def test_describe_nodes_improve():
    """describe_nodes on improve_workflow returns all nodes with non-empty fields."""
    wf = improve_workflow()
    nodes = describe_nodes(wf)

    assert len(nodes) == len(wf.nodes)
    for entry in nodes:
        assert entry["id"], "id should not be empty"
        assert entry["type"], "type should not be empty"
        assert entry["description"], "description should not be empty"

    # Verify topological order: each node appears after its predecessors
    id_order = {entry["id"]: i for i, entry in enumerate(nodes)}
    for edge in wf.edges:
        if edge.condition == VerdictType.RELOOP:
            continue
        if edge.source in id_order and edge.target in id_order:
            assert id_order[edge.source] < id_order[edge.target], (
                f"topo order violated: {edge.source} should come before {edge.target}"
            )


def test_describe_nodes_all_registered():
    """describe_nodes on every registered workflow produces non-empty descriptions."""
    all_wf = register_all()
    for name, wf in all_wf.items():
        nodes = describe_nodes(wf)
        for entry in nodes:
            assert entry["description"], (
                f"workflow '{name}' node '{entry['id']}' has empty description"
            )


def test_describe_nodes_types_correct():
    """Verify type strings match expected patterns for each node type."""
    all_wf = register_all()

    found_types: set[str] = set()
    for wf in all_wf.values():
        nodes = describe_nodes(wf)
        for entry in nodes:
            found_types.add(entry["type"].split("(")[0])

    expected = {"Agent", "Gate", "Fn", "Fork", "Join", "Study"}
    assert expected <= found_types, f"Missing types: {expected - found_types}"


def test_describe_nodes_llm_mode():
    """describe_nodes with use_llm=True returns structured output with clean descriptions."""
    wf = discover_workflow()

    mock_descriptions = [
        {"id": "discover", "description": "Auto-discover eval dimensions for the project"},
        {"id": "gate_discover", "description": "CEO verifies the discovered eval profile"},
        {"id": "redetect", "description": "Re-detect project state after discovery"},
    ]
    mock_stdout = '```json\n' + __import__("json").dumps(mock_descriptions) + '\n```'

    mock_result = AsyncMock()
    mock_result.stdout = mock_stdout

    mock_runner = AsyncMock()
    mock_runner.headless = AsyncMock(return_value=mock_result)

    with patch("factory.runners.get_runner", return_value=mock_runner):
        nodes = describe_nodes(wf, use_llm=True)

    assert len(nodes) == len(wf.nodes)
    for entry in nodes:
        assert entry["id"]
        assert entry["type"]
        assert entry["description"]
        assert "{project_path}" not in entry["description"]
