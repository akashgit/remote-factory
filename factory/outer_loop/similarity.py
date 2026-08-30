"""Novelty filtering, deduplication, and feature extraction for workflows."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import networkx as nx

if TYPE_CHECKING:
    from factory.workflow.primitives import Workflow


def structural_hash(workflow: Workflow) -> str:
    """SHA-256 of the canonical form of a workflow graph.

    Nodes are sorted by id; edges are sorted by (source, target).
    The trigger function is excluded (not serializable).
    """
    nodes_canonical: list[dict[str, object]] = []
    for nid in sorted(workflow.nodes):
        node = workflow.nodes[nid]
        d = node.model_dump(mode="json")
        d["_type"] = type(node).__name__
        nodes_canonical.append(d)

    edges_canonical = sorted(
        [e.model_dump(mode="json") for e in workflow.edges],
        key=lambda e: (e["source"], e["target"]),
    )

    blob = json.dumps(
        {"nodes": nodes_canonical, "edges": edges_canonical},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def _build_nx_graph(workflow: Workflow) -> nx.DiGraph[str]:
    """Build a NetworkX DiGraph from a workflow for analysis."""
    g: nx.DiGraph[str] = nx.DiGraph()
    for nid in workflow.nodes:
        g.add_node(nid, node_type=type(workflow.nodes[nid]).__name__)
    for edge in workflow.edges:
        g.add_edge(edge.source, edge.target)
    return g


def graph_edit_distance(w1: Workflow, w2: Workflow) -> int:
    """Approximate graph edit distance between two workflows.

    Counts: nodes in w1 not in w2, nodes in w2 not in w1,
    edges in w1 not in w2, edges in w2 not in w1,
    plus attribute diffs on common nodes (different type = 1 edit).
    """
    n1 = set(w1.nodes.keys())
    n2 = set(w2.nodes.keys())

    e1 = {(e.source, e.target) for e in w1.edges}
    e2 = {(e.source, e.target) for e in w2.edges}

    dist = len(n1 - n2) + len(n2 - n1) + len(e1 - e2) + len(e2 - e1)

    for nid in n1 & n2:
        if type(w1.nodes[nid]).__name__ != type(w2.nodes[nid]).__name__:
            dist += 1

    return dist


def compute_features(workflow: Workflow) -> tuple[int, ...]:
    """Extract features from a workflow for MAP-Elites grid placement.

    Every mutation type must produce a distinct feature so that different
    mutations land in different cells:

    - NODE_INSERT / NODE_REMOVE → agent_count, depth
    - PARALLELIZE / SERIALIZE  → fork_degree
    - EDGE_REDIRECT            → edge_hash
    - PARAM_MUTATE             → param_hash (timeout, model, etc.)
    - KNOB_MUTATE              → knob_hashes
    - PROMPT_MUTATE            → prompt_hashes
    """
    g = _build_nx_graph(workflow)

    try:
        depth = nx.dag_longest_path_length(g)
    except (nx.NetworkXUnfeasible, nx.NetworkXError):
        depth = len(workflow.nodes)

    fork_degree = 0
    agent_count = 0
    gate_count = 0

    for node in workflow.nodes.values():
        tname = type(node).__name__
        if tname == "ForkNode":
            fork_degree = max(fork_degree, len(node.targets))  # type: ignore[union-attr]
        elif tname == "AgentNode":
            agent_count += 1
        elif tname == "GateNode":
            gate_count += 1

    def _hash_bucket(sig: str, buckets: int = 8) -> int:
        return int(hashlib.sha256(sig.encode()).hexdigest(), 16) % buckets

    # EDGE_REDIRECT: hash the sorted edge list
    edge_sig = ",".join(sorted(f"{e.source}->{e.target}" for e in workflow.edges))

    # PARAM_MUTATE: aggregate hash of (model, timeout) per agent, sorted by node id
    param_sig = "|".join(
        f"{nid}:{getattr(workflow.nodes[nid], 'model', '')}:{getattr(workflow.nodes[nid], 'timeout', 0)}"
        for nid in sorted(workflow.nodes)
        if type(workflow.nodes[nid]).__name__ == "AgentNode"
    )

    # PROMPT_MUTATE: aggregate hash of all prompts, sorted by node id
    prompt_sig = "|".join(
        f"{nid}:{getattr(workflow.nodes[nid], 'prompt_template', '') or ''}"
        for nid in sorted(workflow.nodes)
        if type(workflow.nodes[nid]).__name__ == "AgentNode"
    )

    # KNOB_MUTATE: aggregate hash of all knob values (sorted by key)
    knob_sig = "|".join(
        f"{k}={v}" for k, v in sorted(workflow.knob_values.items())
    ) if workflow.knob_values else ""

    return (
        depth, fork_degree, agent_count, gate_count,
        _hash_bucket(edge_sig),
        _hash_bucket(param_sig, 16),
        _hash_bucket(prompt_sig, 32),
        _hash_bucket(knob_sig, 16),
    )


class NoveltyFilter:
    """Rejects near-duplicate workflows based on hash and edit distance."""

    def __init__(self, min_edit_distance: int = 5, max_archive_size: int = 1000) -> None:
        self.seen_hashes: set[str] = set()
        self.min_edit_distance = min_edit_distance
        self.max_archive_size = max_archive_size
        self._archived_workflows: list[Workflow] = []

    def is_novel(self, workflow: Workflow, threshold: int | None = None) -> bool:
        """Check if a workflow is novel (not seen before).

        Returns False if the structural hash was seen before OR if the
        graph edit distance to any archived workflow is below threshold.
        """
        h = structural_hash(workflow)
        if h in self.seen_hashes:
            return False

        t = threshold if threshold is not None else self.min_edit_distance
        for archived in self._archived_workflows:
            if graph_edit_distance(workflow, archived) < t:
                return False

        return True

    def add(self, workflow: Workflow) -> None:
        """Register a workflow as seen."""
        self.seen_hashes.add(structural_hash(workflow))
        if len(self._archived_workflows) < self.max_archive_size:
            self._archived_workflows.append(workflow)
