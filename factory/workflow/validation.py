"""NetworkX-based graph validation for workflow definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import networkx as nx

if TYPE_CHECKING:
    from factory.workflow.primitives import Workflow


def _validate_start_node(workflow: Workflow, issues: list[str]) -> None:
    if workflow.start_node not in workflow.nodes:
        issues.append(f"start_node '{workflow.start_node}' not in nodes")


def _validate_edges(workflow: Workflow, issues: list[str]) -> None:
    for edge in workflow.edges:
        if edge.source not in workflow.nodes:
            issues.append(f"edge source '{edge.source}' not in nodes")
        if edge.target not in workflow.nodes:
            issues.append(f"edge target '{edge.target}' not in nodes")


def _validate_reachability(
    g: nx.DiGraph, workflow: Workflow, issues: list[str],  # type: ignore[type-arg]
) -> None:
    reachable = nx.descendants(g, workflow.start_node) | {workflow.start_node}
    unreachable = set(workflow.nodes.keys()) - reachable
    for nid in sorted(unreachable):
        issues.append(f"node '{nid}' is unreachable from start_node")


def _validate_cycles(
    g: nx.DiGraph, workflow: Workflow, issues: list[str],  # type: ignore[type-arg]
) -> None:
    cycles = list(nx.simple_cycles(g))
    for cycle in cycles:
        cycle_edges = []
        for i in range(len(cycle)):
            src = cycle[i]
            tgt = cycle[(i + 1) % len(cycle)]
            cycle_edges.append((src, tgt))

        has_gate_with_limit = False
        for src, tgt in cycle_edges:
            if type(workflow.nodes.get(src)).__name__ == "GateNode":
                for edge in workflow.edges:
                    if edge.source == src and edge.target == tgt and edge.condition is not None:
                        has_gate_with_limit = True
                        break
            if has_gate_with_limit:
                break

        if not has_gate_with_limit:
            cycle_str = " -> ".join(cycle + [cycle[0]])
            issues.append(f"cycle without gate condition: {cycle_str}")


def _validate_data_dependencies(
    g: nx.DiGraph, workflow: Workflow, issues: list[str],  # type: ignore[type-arg]
) -> None:
    for nid, node in workflow.nodes.items():
        if node.reads:
            predecessors = nx.ancestors(g, nid)
            available_writes: set[str] = set()
            for pred_id in predecessors:
                pred_node = workflow.nodes.get(pred_id)
                if pred_node:
                    available_writes |= pred_node.writes
            missing = node.reads - available_writes
            if missing:
                issues.append(
                    f"node '{nid}' reads {missing} but no predecessor writes them"
                )


def _validate_fork_join_nodes(workflow: Workflow, issues: list[str]) -> None:
    for nid, node in workflow.nodes.items():
        if type(node).__name__ == "ForkNode":
            for t in node.targets:  # type: ignore[union-attr]
                if t not in workflow.nodes:
                    issues.append(f"fork '{nid}' target '{t}' not in nodes")

        if type(node).__name__ == "JoinNode":
            for s in node.sources:  # type: ignore[union-attr]
                if s not in workflow.nodes:
                    issues.append(f"join '{nid}' source '{s}' not in nodes")

        if type(node).__name__ == "SubgraphForkNode":
            entry = node.subgraph_entry  # type: ignore[union-attr]
            exit_node = node.subgraph_exit  # type: ignore[union-attr]
            if entry not in workflow.nodes:
                issues.append(f"subgraph_fork '{nid}' entry '{entry}' not in nodes")
            if exit_node not in workflow.nodes:
                issues.append(f"subgraph_fork '{nid}' exit '{exit_node}' not in nodes")


def validate_workflow(workflow: Workflow) -> list[str]:
    """Validate a workflow graph. Returns a list of issues (empty = valid)."""
    issues: list[str] = []

    _validate_start_node(workflow, issues)
    _validate_edges(workflow, issues)

    if issues:
        return issues

    g: nx.DiGraph[str] = nx.DiGraph()
    nodes = workflow.nodes
    for nid in nodes:
        g.add_node(nid)
    for edge in workflow.edges:
        g.add_edge(edge.source, edge.target, condition=edge.condition)

    # Add implicit edges for SubgraphForkNode: fork → subgraph_entry
    # so subgraph nodes are reachable in the graph
    for nid, node in nodes.items():
        if type(node).__name__ == "SubgraphForkNode":
            entry = node.subgraph_entry  # type: ignore[union-attr]
            if entry in nodes:
                g.add_edge(nid, entry, condition=None)

    _validate_reachability(g, workflow, issues)
    _validate_cycles(g, workflow, issues)
    _validate_data_dependencies(g, workflow, issues)
    _validate_fork_join_nodes(workflow, issues)

    for nid, node in nodes.items():
        if type(node).__name__ == "SubgraphForkNode":
            entry = node.subgraph_entry  # type: ignore[union-attr]
            exit_node = node.subgraph_exit  # type: ignore[union-attr]
            if entry in nodes and exit_node in nodes:
                if not nx.has_path(g, entry, exit_node):
                    issues.append(
                        f"subgraph_fork '{nid}': no path from entry '{entry}' to exit '{exit_node}'"
                    )

    return issues
