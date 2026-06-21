"""NetworkX-based graph validation for workflow definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import networkx as nx

if TYPE_CHECKING:
    from factory.workflow.primitives import Workflow


def validate_workflow(workflow: Workflow) -> list[str]:
    """Validate a workflow graph. Returns a list of issues (empty = valid)."""
    from factory.workflow.primitives import ForkNode, GateNode, JoinNode

    issues: list[str] = []
    nodes = workflow.nodes
    edges = workflow.edges

    if workflow.start_node not in nodes:
        issues.append(f"start_node '{workflow.start_node}' not in nodes")

    for edge in edges:
        if edge.source not in nodes:
            issues.append(f"edge source '{edge.source}' not in nodes")
        if edge.target not in nodes:
            issues.append(f"edge target '{edge.target}' not in nodes")

    if issues:
        return issues

    g: nx.DiGraph[str] = nx.DiGraph()
    for nid in nodes:
        g.add_node(nid)
    for edge in edges:
        g.add_edge(edge.source, edge.target, condition=edge.condition)

    reachable = nx.descendants(g, workflow.start_node) | {workflow.start_node}
    unreachable = set(nodes.keys()) - reachable
    for nid in sorted(unreachable):
        issues.append(f"node '{nid}' is unreachable from start_node")

    cycles = list(nx.simple_cycles(g))
    for cycle in cycles:
        cycle_edges = []
        for i in range(len(cycle)):
            src = cycle[i]
            tgt = cycle[(i + 1) % len(cycle)]
            cycle_edges.append((src, tgt))

        has_gate_with_limit = False
        for src, tgt in cycle_edges:
            if isinstance(nodes.get(src), GateNode):
                for edge in edges:
                    if edge.source == src and edge.target == tgt and edge.condition is not None:
                        has_gate_with_limit = True
                        break
            if has_gate_with_limit:
                break

        if not has_gate_with_limit:
            cycle_str = " -> ".join(cycle + [cycle[0]])
            issues.append(f"cycle without gate condition: {cycle_str}")

    for nid, node in nodes.items():
        if node.reads:
            predecessors = nx.ancestors(g, nid)
            available_writes: set[str] = set()
            for pred_id in predecessors:
                pred_node = nodes.get(pred_id)
                if pred_node:
                    available_writes |= pred_node.writes
            missing = node.reads - available_writes
            if missing:
                issues.append(
                    f"node '{nid}' reads {missing} but no predecessor writes them"
                )

    for nid, node in nodes.items():
        if isinstance(node, ForkNode):
            for t in node.targets:
                if t not in nodes:
                    issues.append(f"fork '{nid}' target '{t}' not in nodes")

        if isinstance(node, JoinNode):
            for s in node.sources:
                if s not in nodes:
                    issues.append(f"join '{nid}' source '{s}' not in nodes")

    return issues
