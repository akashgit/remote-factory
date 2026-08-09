"""Workflow composition — functions for building new workflows from existing ones.

Provides prefix_nodes, find_terminal_nodes, compose_serial, trim_nodes,
validate_composition, and describe_nodes. All mutating functions return new
Workflow objects and call validate_graph() before returning.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import structlog

from factory.workflow.primitives import (
    AgentNode,
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    Study,
    VerdictType,
    Workflow,
)

log = structlog.get_logger()


def prefix_nodes(wf: Workflow, prefix: str) -> Workflow:
    """Return a new Workflow with all node IDs prefixed as ``{prefix}_{id}``.

    Deep-copies all nodes. Rewrites edge sources/targets, start_node,
    ForkNode.targets, and JoinNode.sources. Sets trigger to None.
    Calls validate_graph() before returning.
    """
    rename = {nid: f"{prefix}_{nid}" for nid in wf.nodes}
    return _apply_rename(wf, rename, name=wf.name)


def find_terminal_nodes(wf: Workflow) -> list[str]:
    """Return node IDs that have no outgoing unconditional edges."""
    nodes_with_unconditional_out: set[str] = set()
    for edge in wf.edges:
        if edge.condition is None:
            nodes_with_unconditional_out.add(edge.source)
    return [nid for nid in wf.nodes if nid not in nodes_with_unconditional_out]


def compose_serial(
    w1: Workflow,
    w2: Workflow,
    *,
    end_node_w1: str,
    start_node_w2: str | None = None,
    name: str,
    rename: dict[str, str] | None = None,
) -> Workflow:
    """Chain two workflows end-to-end.

    Deep-copies all nodes from both workflows. Applies ``rename`` dict to w2's
    node IDs, detects remaining ID conflicts, adds a bridge edge from
    ``end_node_w1`` to ``start_node_w2``, and validates the result.

    Raises ValueError if ID conflicts remain after rename or if the graph
    is invalid.
    """
    if end_node_w1 not in w1.nodes:
        raise ValueError(f"end_node_w1 '{end_node_w1}' not in w1 nodes")

    actual_start_w2 = start_node_w2 or w2.start_node
    rename = rename or {}

    # Build the rename mapping for w2
    def _renamed(nid: str) -> str:
        return rename.get(nid, nid)

    # Detect ID conflicts after rename
    w2_renamed_ids = {_renamed(nid) for nid in w2.nodes}
    conflicts = set(w1.nodes.keys()) & w2_renamed_ids
    if conflicts:
        raise ValueError(
            f"ID conflicts between w1 and w2 (after rename): {sorted(conflicts)}"
        )

    # Deep-copy w1 nodes
    nodes: dict[str, Any] = {}
    for nid, node in w1.nodes.items():
        nodes[nid] = node.model_copy(deep=True)

    # Deep-copy w2 nodes with rename
    for nid, node in w2.nodes.items():
        new_id = _renamed(nid)
        copied = node.model_copy(deep=True)
        copied = _rename_node(copied, nid, new_id, rename)
        nodes[new_id] = copied

    # Merge edges
    edges: list[Edge] = [e.model_copy(deep=True) for e in w1.edges]
    for e in w2.edges:
        edges.append(Edge(
            source=_renamed(e.source),
            target=_renamed(e.target),
            condition=e.condition,
        ))

    # Bridge edge
    renamed_start_w2 = _renamed(actual_start_w2)
    edges.append(Edge(source=end_node_w1, target=renamed_start_w2))

    result = Workflow(
        name=name,
        nodes=nodes,
        edges=edges,
        start_node=w1.start_node,
        trigger=None,
    )

    issues = result.validate_graph()
    if issues:
        raise ValueError(f"Composed workflow validation failed: {issues}")

    return result


def trim_nodes(wf: Workflow, node_ids: set[str]) -> Workflow:
    """Remove specified nodes and reconnect predecessors to successors.

    MVP scope: linear only (1 unconditional in-edge, 1 unconditional out-edge).
    Raises ValueError for ForkNode, JoinNode, nodes with conditional edges,
    or data flow breaks.
    """
    for nid in node_ids:
        if nid not in wf.nodes:
            raise ValueError(f"node '{nid}' not in workflow")
        node = wf.nodes[nid]

        if isinstance(node, ForkNode):
            raise ValueError(f"structural nodes cannot be trimmed: '{nid}' is a ForkNode")
        if isinstance(node, JoinNode):
            raise ValueError(f"structural nodes cannot be trimmed: '{nid}' is a JoinNode")

        # Check conditional edges
        in_edges = [e for e in wf.edges if e.target == nid]
        out_edges = [e for e in wf.edges if e.source == nid]

        conditional_in = [e for e in in_edges if e.condition is not None]
        conditional_out = [e for e in out_edges if e.condition is not None]
        if conditional_in or conditional_out:
            raise ValueError(
                f"nodes with conditional edges cannot be trimmed: '{nid}'; "
                "use subgraph() instead"
            )

        unconditional_in = [e for e in in_edges if e.condition is None]
        unconditional_out = [e for e in out_edges if e.condition is None]

        if len(unconditional_in) != 1:
            raise ValueError(
                f"node '{nid}' has {len(unconditional_in)} unconditional in-edges "
                "(expected exactly 1)"
            )
        if len(unconditional_out) != 1:
            raise ValueError(
                f"node '{nid}' has {len(unconditional_out)} unconditional out-edges "
                "(expected exactly 1)"
            )

        # Check data flow: if trimmed node writes files that downstream nodes
        # read exclusively from this node
        if node.writes:
            successor_id = unconditional_out[0].target
            _check_data_flow(wf, nid, successor_id, node_ids)

    # Build new edges: remove edges touching trimmed nodes, add bridge edges
    new_edges: list[Edge] = []
    bridge_edges: list[Edge] = []

    for nid in node_ids:
        in_edges = [e for e in wf.edges if e.target == nid and e.condition is None]
        out_edges = [e for e in wf.edges if e.source == nid and e.condition is None]
        predecessor = in_edges[0].source
        successor = out_edges[0].target
        bridge_edges.append(Edge(source=predecessor, target=successor))

    for e in wf.edges:
        if e.source not in node_ids and e.target not in node_ids:
            new_edges.append(e.model_copy(deep=True))

    new_edges.extend(bridge_edges)

    # Deep-copy nodes minus trimmed
    nodes: dict[str, Any] = {}
    for nid, node in wf.nodes.items():
        if nid not in node_ids:
            nodes[nid] = node.model_copy(deep=True)

    # Determine start_node
    start = wf.start_node
    if start in node_ids:
        out_edges = [e for e in wf.edges if e.source == start and e.condition is None]
        start = out_edges[0].target

    result = Workflow(
        name=wf.name,
        nodes=nodes,
        edges=new_edges,
        start_node=start,
        trigger=None,
    )

    issues = result.validate_graph()
    if issues:
        raise ValueError(f"Trimmed workflow validation failed: {issues}")

    return result


def validate_composition(wf: Workflow) -> list[str]:
    """Run graph validation plus composition-specific checks.

    Returns combined list of issues (empty = valid).
    """
    issues = wf.validate_graph()

    for nid, node in wf.nodes.items():
        if isinstance(node, ForkNode):
            for t in node.targets:
                if t not in wf.nodes:
                    issues.append(f"ForkNode '{nid}' target '{t}' not in nodes")
        if isinstance(node, JoinNode):
            for s in node.sources:
                if s not in wf.nodes:
                    issues.append(f"JoinNode '{nid}' source '{s}' not in nodes")

    return issues


def describe_nodes(
    wf: Workflow,
    *,
    use_llm: bool = False,
) -> list[dict[str, str]]:
    """Return a list of {id, type, description} dicts for every node, topo-sorted.

    Default mode extracts descriptions from node metadata. LLM mode makes a
    single runner call for richer summaries.
    """
    sorted_ids = _topological_sort(wf)

    if use_llm:
        return _describe_nodes_llm(wf, sorted_ids)
    return _describe_nodes_fast(wf, sorted_ids)


# ── internal helpers ─────────────────────────────────────────────


def _rename_node(
    node: Any,
    old_id: str,
    new_id: str,
    rename: dict[str, str],
) -> Any:
    """Rename a node's ID and update internal references."""
    def _r(nid: str) -> str:
        return rename.get(nid, nid)

    updates: dict[str, Any] = {"id": new_id}

    if isinstance(node, ForkNode):
        updates["targets"] = [_r(t) for t in node.targets]
    elif isinstance(node, JoinNode):
        updates["sources"] = [_r(s) for s in node.sources]

    return node.model_copy(update=updates)


def _apply_rename(
    wf: Workflow,
    rename: dict[str, str],
    *,
    name: str,
) -> Workflow:
    """Apply a rename mapping to an entire workflow."""
    def _r(nid: str) -> str:
        return rename.get(nid, nid)

    nodes: dict[str, Any] = {}
    for nid, node in wf.nodes.items():
        new_id = _r(nid)
        copied = node.model_copy(deep=True)
        copied = _rename_node(copied, nid, new_id, rename)
        nodes[new_id] = copied

    edges = [
        Edge(source=_r(e.source), target=_r(e.target), condition=e.condition)
        for e in wf.edges
    ]

    result = Workflow(
        name=name,
        nodes=nodes,
        edges=edges,
        start_node=_r(wf.start_node),
        trigger=None,
    )

    issues = result.validate_graph()
    if issues:
        raise ValueError(f"Renamed workflow validation failed: {issues}")

    return result


def _check_data_flow(
    wf: Workflow,
    trimmed_id: str,
    successor_id: str,
    all_trimmed: set[str],
) -> None:
    """Check that trimming a node doesn't break data flow.

    For each file the trimmed node writes, check if any non-trimmed successor
    reads it and no other non-trimmed predecessor writes it.
    """
    import networkx as nx

    trimmed_node = wf.nodes[trimmed_id]

    g: nx.DiGraph[str] = nx.DiGraph()
    for nid in wf.nodes:
        g.add_node(nid)
    for edge in wf.edges:
        g.add_edge(edge.source, edge.target)

    for written_file in trimmed_node.writes:
        # Find all non-trimmed descendants that read this file
        descendants = nx.descendants(g, trimmed_id)
        readers = [
            nid for nid in descendants
            if nid not in all_trimmed and written_file in wf.nodes[nid].reads
        ]
        if not readers:
            continue

        # Check if any other non-trimmed predecessor writes this file
        other_writers = [
            nid for nid, node in wf.nodes.items()
            if nid != trimmed_id
            and nid not in all_trimmed
            and written_file in node.writes
        ]

        # For each reader, check if at least one other writer is an ancestor
        for reader_id in readers:
            reader_ancestors = nx.ancestors(g, reader_id)
            has_other_source = any(
                w in reader_ancestors for w in other_writers
            )
            if not has_other_source:
                raise ValueError(
                    f"trimming '{trimmed_id}' breaks data flow: '{reader_id}' "
                    f"reads '{written_file}' which only '{trimmed_id}' writes"
                )


def _topological_sort(wf: Workflow) -> list[str]:
    """Topological sort of node IDs, ignoring RELOOP back-edges."""
    adj: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {nid: 0 for nid in wf.nodes}

    for edge in wf.edges:
        if edge.condition == VerdictType.RELOOP:
            continue
        adj[edge.source].append(edge.target)
        in_degree[edge.target] = in_degree.get(edge.target, 0) + 1

    queue: deque[str] = deque()
    for nid in wf.nodes:
        if in_degree.get(nid, 0) == 0:
            queue.append(nid)

    if not queue:
        queue.append(wf.start_node)

    ordered: list[str] = []
    visited: set[str] = set()

    while queue:
        nid = queue.popleft()
        if nid in visited:
            continue
        visited.add(nid)
        ordered.append(nid)

        for target in adj.get(nid, []):
            in_degree[target] -= 1
            if in_degree[target] <= 0 and target not in visited:
                queue.append(target)

    for nid in wf.nodes:
        if nid not in visited:
            ordered.append(nid)

    return ordered


def _node_type_str(node: Any) -> str:
    """Return a descriptive type string for a node."""
    if isinstance(node, Study):
        return "Study"
    if isinstance(node, AgentNode):
        return f"Agent({node.role.value})"
    if isinstance(node, GateNode):
        return f"Gate({node.evaluator_type})"
    if isinstance(node, ForkNode):
        return f"Fork({len(node.targets)})"
    if isinstance(node, JoinNode):
        return f"Join({len(node.sources)})"
    if isinstance(node, FnNode):
        return "Fn"
    return "Unknown"


def _first_sentence(text: str) -> str:
    """Extract the first sentence from a text block."""
    if not text:
        return ""
    text = text.strip()
    for sep in (". ", ".\n", ".\t"):
        idx = text.find(sep)
        if idx != -1:
            return text[: idx + 1]
    if text.endswith("."):
        return text
    # No period found — return whole text truncated
    if len(text) > 100:
        return text[:97] + "..."
    return text


def _describe_nodes_fast(
    wf: Workflow,
    sorted_ids: list[str],
) -> list[dict[str, str]]:
    """Fast extraction mode — derive descriptions from node metadata."""
    result: list[dict[str, str]] = []

    for nid in sorted_ids:
        node = wf.nodes[nid]
        type_str = _node_type_str(node)

        if isinstance(node, Study):
            desc = node.command[:80] if node.command else nid
        elif isinstance(node, AgentNode):
            if node.prompt_template:
                desc = _first_sentence(node.prompt_template)
            else:
                desc = node.role.value
        elif isinstance(node, GateNode):
            if node.gate_prompt:
                desc = _first_sentence(node.gate_prompt)
            elif node.evaluator_command:
                desc = node.evaluator_command[:80]
            else:
                desc = node.evaluator_type
        elif isinstance(node, ForkNode):
            desc = "Fork to: " + ", ".join(node.targets)
        elif isinstance(node, JoinNode):
            desc = "Join from: " + ", ".join(node.sources)
        elif isinstance(node, FnNode):
            desc = node.command[:80] if node.command else nid
        else:
            desc = nid

        result.append({"id": nid, "type": type_str, "description": desc})

    return result


def _describe_nodes_llm(
    wf: Workflow,
    sorted_ids: list[str],
) -> list[dict[str, str]]:
    """LLM mode — make a single runner call for rich node descriptions."""
    from factory.models import AgentRunRequest
    from factory.runners import get_runner

    # Build the batch prompt
    node_specs: list[dict[str, Any]] = []
    for nid in sorted_ids:
        node = wf.nodes[nid]
        spec: dict[str, Any] = {
            "id": nid,
            "type": _node_type_str(node),
        }

        if isinstance(node, AgentNode):
            if node.prompt_template:
                spec["prompt_template"] = node.prompt_template[:200]
            spec["role"] = node.role.value
        elif isinstance(node, GateNode):
            if node.gate_prompt:
                spec["gate_prompt"] = node.gate_prompt[:200]
            spec["evaluator_type"] = node.evaluator_type
        elif isinstance(node, FnNode):
            if node.command:
                spec["command"] = node.command[:200]
        elif isinstance(node, ForkNode):
            spec["targets"] = node.targets
        elif isinstance(node, JoinNode):
            spec["sources"] = node.sources

        if node.reads:
            spec["reads"] = sorted(node.reads)
        if node.writes:
            spec["writes"] = sorted(node.writes)

        node_specs.append(spec)

    prompt = (
        "You are describing nodes in a workflow graph. "
        "For each node below, write a clean one-line description of what it does. "
        "Do NOT include raw template syntax like {project_path}. "
        "Return valid JSON: a list of objects with 'id' and 'description' keys.\n\n"
        f"Nodes:\n{json.dumps(node_specs, indent=2)}"
    )

    runner = get_runner()
    request = AgentRunRequest(
        prompt="Describe workflow nodes concisely.",
        task=prompt,
        cwd=Path.cwd(),
        timeout=60.0,
        role="describe_nodes",
    )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = pool.submit(asyncio.run, runner.headless(request)).result()
    else:
        result = asyncio.run(runner.headless(request))

    # Parse the LLM response
    try:
        raw = result.stdout.strip()
        # Extract JSON from the response (may be wrapped in markdown code block)
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        descriptions: list[dict[str, str]] = json.loads(raw)
        desc_map = {d["id"]: d["description"] for d in descriptions}
    except (json.JSONDecodeError, KeyError, IndexError):
        log.warning("describe_nodes.llm_parse_failed", stdout=result.stdout[:200])
        return _describe_nodes_fast(wf, sorted_ids)

    # Build result with type info and fallback to fast extraction
    output: list[dict[str, str]] = []
    for nid in sorted_ids:
        node = wf.nodes[nid]
        type_str = _node_type_str(node)
        desc = desc_map.get(nid, "")
        if not desc:
            fast = _describe_nodes_fast(wf, [nid])
            desc = fast[0]["description"] if fast else nid
        output.append({"id": nid, "type": type_str, "description": desc})

    return output
