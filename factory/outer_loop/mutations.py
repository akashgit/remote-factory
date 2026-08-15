"""Structured graph mutation operators and strategy protocol for workflow evolution."""

from __future__ import annotations

import re
import random
from typing import Protocol, runtime_checkable

import networkx as nx
import structlog

from factory.outer_loop.designer import populate_prompt
from factory.outer_loop.models import MutationRecord, MutationType
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    ForkNode,
    JoinNode,
    NodeType,
    Workflow,
)

log = structlog.get_logger()

_FROZEN_SEGMENT_PATTERNS = [
    re.compile(r"MUST\s+NOT", re.IGNORECASE),
    re.compile(r"MUST\s+", re.IGNORECASE),
    re.compile(r"FORBIDDEN", re.IGNORECASE),
    re.compile(r"DO\s+NOT", re.IGNORECASE),
    re.compile(r"NEVER\s+", re.IGNORECASE),
]


@runtime_checkable
class MutationStrategy(Protocol):
    """Protocol for pluggable mutation operator selection."""

    def select_operator(
        self, parent: Workflow, generation: int, archive_stats: dict[str, object]
    ) -> MutationType: ...

    def get_mutation_rate(self, generation: int) -> float: ...

    def get_designer_ratio(self, generation: int) -> float: ...


class WeightedRandomStrategy:
    """Default mutation strategy: select operators by configurable weights."""

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        mutation_rate: float = 0.3,
        designer_ratio: float = 0.3,
    ) -> None:
        self.weights = weights or {
            MutationType.NODE_INSERT.value: 0.15,
            MutationType.NODE_REMOVE.value: 0.15,
            MutationType.EDGE_REDIRECT.value: 0.2,
            MutationType.PARALLELIZE.value: 0.15,
            MutationType.SERIALIZE.value: 0.1,
            MutationType.PARAM_MUTATE.value: 0.1,
            MutationType.PROMPT_MUTATE.value: 0.15,
        }
        self._mutation_rate = mutation_rate
        self._designer_ratio = designer_ratio

    def select_operator(
        self, parent: Workflow, generation: int, archive_stats: dict[str, object]
    ) -> MutationType:
        types = list(MutationType)
        w = [self.weights.get(t.value, 0.1) for t in types]
        return random.choices(types, weights=w, k=1)[0]

    def get_mutation_rate(self, generation: int) -> float:
        return self._mutation_rate

    def get_designer_ratio(self, generation: int) -> float:
        return self._designer_ratio

    def get_operator_weights(self) -> dict[str, float]:
        return dict(self.weights)

    def on_plateau(self) -> None:
        """Increase mutation rate when evolution stalls."""
        self._mutation_rate = min(self._mutation_rate + 0.2, 0.8)

    def on_improvement(self) -> None:
        """Reset mutation rate after improvement."""
        self._mutation_rate = 0.3


def validate_and_repair(workflow: Workflow) -> Workflow | None:
    """Validate a mutated workflow and attempt repair. Returns None if irreparable."""
    g: nx.DiGraph[str] = nx.DiGraph()
    for nid in workflow.nodes:
        g.add_node(nid)
    for edge in workflow.edges:
        if edge.source in workflow.nodes and edge.target in workflow.nodes:
            g.add_edge(edge.source, edge.target)

    if workflow.start_node not in workflow.nodes:
        return None

    # Prune unreachable nodes
    reachable = nx.descendants(g, workflow.start_node) | {workflow.start_node}
    unreachable = set(workflow.nodes.keys()) - reachable
    for nid in unreachable:
        del workflow.nodes[nid]
    workflow.edges = [
        e for e in workflow.edges
        if e.source in workflow.nodes and e.target in workflow.nodes
    ]

    # Rebuild graph and check for cycles without gate conditions
    g2: nx.DiGraph[str] = nx.DiGraph()
    for nid in workflow.nodes:
        g2.add_node(nid)
    for edge in workflow.edges:
        g2.add_edge(edge.source, edge.target)

    for cycle in nx.simple_cycles(g2):
        has_gated_edge = False
        for i in range(len(cycle)):
            src = cycle[i]
            tgt = cycle[(i + 1) % len(cycle)]
            if type(workflow.nodes.get(src)).__name__ == "GateNode":
                for e in workflow.edges:
                    if e.source == src and e.target == tgt and e.condition is not None:
                        has_gated_edge = True
                        break
            if has_gated_edge:
                break
        if not has_gated_edge:
            return None

    # Verify reads/writes chain
    for nid, node in workflow.nodes.items():
        if node.reads:
            ancestors = nx.ancestors(g2, nid) if nid in g2 else set()
            available_writes: set[str] = set()
            for anc in ancestors:
                anc_node = workflow.nodes.get(anc)
                if anc_node:
                    available_writes |= anc_node.writes
            broken_reads = node.reads - available_writes
            if broken_reads:
                node_copy = node.model_copy(update={"reads": node.reads - broken_reads})
                workflow.nodes[nid] = node_copy  # type: ignore[assignment]

    return workflow


def _is_frozen(node_id: str, frozen_nodes: set[str]) -> bool:
    return node_id in frozen_nodes


def _deep_copy_workflow(workflow: Workflow) -> Workflow:
    """Deep copy a workflow for mutation."""
    nodes: dict[str, NodeType] = {}
    for nid, node in workflow.nodes.items():
        nodes[nid] = node.model_copy(deep=True)
    edges = [e.model_copy(deep=True) for e in workflow.edges]
    return Workflow(
        name=workflow.name,
        nodes=nodes,
        edges=edges,
        start_node=workflow.start_node,
        terminal=workflow.terminal,
    )


def insert_node(
    workflow: Workflow,
    new_node: NodeType,
    after_node_id: str,
    *,
    frozen_nodes: set[str] | None = None,
) -> tuple[Workflow, MutationRecord] | None:
    """Insert a new node after an existing node, reconnecting edges."""
    frozen = frozen_nodes or set()
    if _is_frozen(after_node_id, frozen):
        return None

    wf = _deep_copy_workflow(workflow)
    if after_node_id not in wf.nodes:
        return None

    wf.nodes[new_node.id] = new_node

    outgoing = [e for e in wf.edges if e.source == after_node_id]
    if not outgoing:
        wf.edges.append(Edge(source=after_node_id, target=new_node.id))
    else:
        first_edge = outgoing[0]
        old_target = first_edge.target
        wf.edges = [e for e in wf.edges if not (e.source == after_node_id and e.target == old_target and e.condition is None)]
        wf.edges.append(Edge(source=after_node_id, target=new_node.id))
        wf.edges.append(Edge(source=new_node.id, target=old_target))

    result = validate_and_repair(wf)
    if result is None:
        return None

    record = MutationRecord(
        operator=MutationType.NODE_INSERT,
        target_node=new_node.id,
        before={},
        after={"inserted_after": after_node_id},
        rationale=f"Inserted {new_node.id} after {after_node_id}",
    )
    return result, record


def remove_node(
    workflow: Workflow,
    node_id: str,
    *,
    frozen_nodes: set[str] | None = None,
) -> tuple[Workflow, MutationRecord] | None:
    """Remove a node and short-circuit its edges."""
    frozen = frozen_nodes or set()
    if _is_frozen(node_id, frozen):
        return None

    wf = _deep_copy_workflow(workflow)
    if node_id not in wf.nodes or node_id == wf.start_node:
        return None

    incoming_sources = [e.source for e in wf.edges if e.target == node_id]
    outgoing_targets = [e.target for e in wf.edges if e.source == node_id]

    wf.edges = [e for e in wf.edges if e.source != node_id and e.target != node_id]

    for src in incoming_sources:
        for tgt in outgoing_targets:
            if not any(e.source == src and e.target == tgt for e in wf.edges):
                wf.edges.append(Edge(source=src, target=tgt))

    del wf.nodes[node_id]

    result = validate_and_repair(wf)
    if result is None:
        return None

    record = MutationRecord(
        operator=MutationType.NODE_REMOVE,
        target_node=node_id,
        before={"node_existed": True},
        after={"short_circuited": True},
        rationale=f"Removed {node_id}, short-circuited edges",
    )
    return result, record


def redirect_edge(
    workflow: Workflow,
    source_id: str,
    old_target_id: str,
    new_target_id: str,
    *,
    frozen_nodes: set[str] | None = None,
) -> tuple[Workflow, MutationRecord] | None:
    """Redirect an edge from old_target to new_target."""
    frozen = frozen_nodes or set()
    if _is_frozen(source_id, frozen):
        return None

    wf = _deep_copy_workflow(workflow)
    if new_target_id not in wf.nodes:
        return None

    found = False
    new_edges: list[Edge] = []
    for e in wf.edges:
        if e.source == source_id and e.target == old_target_id and not found:
            new_edges.append(Edge(source=source_id, target=new_target_id, condition=e.condition))
            found = True
        else:
            new_edges.append(e)

    if not found:
        return None

    wf.edges = new_edges
    result = validate_and_repair(wf)
    if result is None:
        return None

    record = MutationRecord(
        operator=MutationType.EDGE_REDIRECT,
        target_node=source_id,
        before={"target": old_target_id},
        after={"target": new_target_id},
        rationale=f"Redirected edge from {source_id}: {old_target_id} → {new_target_id}",
    )
    return result, record


def parallelize(
    workflow: Workflow,
    node_ids: list[str],
    *,
    frozen_nodes: set[str] | None = None,
) -> tuple[Workflow, MutationRecord] | None:
    """Convert sequential nodes to parallel execution via ForkNode + JoinNode."""
    frozen = frozen_nodes or set()
    if any(_is_frozen(nid, frozen) for nid in node_ids):
        return None
    if len(node_ids) < 2:
        return None

    wf = _deep_copy_workflow(workflow)
    for nid in node_ids:
        if nid not in wf.nodes:
            return None

    fork_id = f"fork_{'_'.join(node_ids[:2])}"
    join_id = f"join_{'_'.join(node_ids[:2])}"

    first_node = node_ids[0]
    last_node = node_ids[-1]

    predecessors = {e.source for e in wf.edges if e.target == first_node}
    successors = {e.target for e in wf.edges if e.source == last_node}

    for nid in node_ids:
        wf.edges = [e for e in wf.edges if e.source != nid and e.target != nid]

    wf.nodes[fork_id] = ForkNode(id=fork_id, targets=node_ids)
    wf.nodes[join_id] = JoinNode(id=join_id, sources=node_ids)

    for pred in predecessors:
        wf.edges.append(Edge(source=pred, target=fork_id))

    for nid in node_ids:
        wf.edges.append(Edge(source=fork_id, target=nid))
        wf.edges.append(Edge(source=nid, target=join_id))

    for succ in successors:
        wf.edges.append(Edge(source=join_id, target=succ))

    if wf.start_node == first_node:
        wf.start_node = fork_id

    result = validate_and_repair(wf)
    if result is None:
        return None

    record = MutationRecord(
        operator=MutationType.PARALLELIZE,
        target_node=fork_id,
        before={"sequential": node_ids},
        after={"parallel": node_ids},
        rationale=f"Parallelized {node_ids}",
    )
    return result, record


def serialize(
    workflow: Workflow,
    fork_id: str,
    *,
    frozen_nodes: set[str] | None = None,
) -> tuple[Workflow, MutationRecord] | None:
    """Collapse a fork/join pair back into sequential execution."""
    frozen = frozen_nodes or set()
    if _is_frozen(fork_id, frozen):
        return None

    wf = _deep_copy_workflow(workflow)
    fork_node = wf.nodes.get(fork_id)
    if fork_node is None or type(fork_node).__name__ != "ForkNode":
        return None

    targets = fork_node.targets  # type: ignore[union-attr]

    join_id: str | None = None
    for nid, node in wf.nodes.items():
        if type(node).__name__ == "JoinNode":
            sources = node.sources  # type: ignore[union-attr]
            if set(sources) == set(targets):
                join_id = nid
                break

    if join_id is None:
        return None

    predecessors = {e.source for e in wf.edges if e.target == fork_id}
    successors = {e.target for e in wf.edges if e.source == join_id}

    wf.edges = [
        e for e in wf.edges
        if e.source != fork_id and e.target != fork_id
        and e.source != join_id and e.target != join_id
        and not (e.source in targets and e.target == join_id)
    ]

    del wf.nodes[fork_id]
    del wf.nodes[join_id]

    chain = list(targets)
    for pred in predecessors:
        wf.edges.append(Edge(source=pred, target=chain[0]))

    for i in range(len(chain) - 1):
        wf.edges.append(Edge(source=chain[i], target=chain[i + 1]))

    for succ in successors:
        wf.edges.append(Edge(source=chain[-1], target=succ))

    if wf.start_node == fork_id:
        wf.start_node = chain[0]

    result = validate_and_repair(wf)
    if result is None:
        return None

    record = MutationRecord(
        operator=MutationType.SERIALIZE,
        target_node=fork_id,
        before={"parallel": list(targets)},
        after={"sequential": chain},
        rationale=f"Serialized fork {fork_id}",
    )
    return result, record


def mutate_params(
    workflow: Workflow,
    node_id: str,
    changes: dict[str, object],
    *,
    frozen_nodes: set[str] | None = None,
) -> tuple[Workflow, MutationRecord] | None:
    """Change parameters on a node (timeout, model, max_iterations)."""
    frozen = frozen_nodes or set()
    if _is_frozen(node_id, frozen):
        return None

    wf = _deep_copy_workflow(workflow)
    node = wf.nodes.get(node_id)
    if node is None:
        return None

    allowed_params = {"timeout", "model", "max_iterations", "blocking"}
    filtered_changes = {k: v for k, v in changes.items() if k in allowed_params}
    if not filtered_changes:
        return None

    before: dict[str, object] = {}
    for k in filtered_changes:
        if hasattr(node, k):
            before[k] = getattr(node, k)

    try:
        updated_node = node.model_copy(update=filtered_changes)
        wf.nodes[node_id] = updated_node  # type: ignore[assignment]
    except Exception:
        return None

    result = validate_and_repair(wf)
    if result is None:
        return None

    record = MutationRecord(
        operator=MutationType.PARAM_MUTATE,
        target_node=node_id,
        before=before,
        after=dict(filtered_changes),
        rationale=f"Changed params on {node_id}: {filtered_changes}",
    )
    return result, record


def apply_random_mutation(
    workflow: Workflow,
    strategy: MutationStrategy,
    generation: int,
    *,
    frozen_nodes: set[str] | None = None,
    archive_stats: dict[str, object] | None = None,
    max_attempts: int = 10,
) -> tuple[Workflow, MutationRecord] | None:
    """Apply a random mutation using the given strategy. Retries on failure."""
    frozen = frozen_nodes or set()
    stats = archive_stats or {}

    for _ in range(max_attempts):
        op = strategy.select_operator(workflow, generation, stats)
        result = _try_mutation(workflow, op, frozen)
        if result is not None:
            return result

    return None


def _try_mutation(
    workflow: Workflow,
    op: MutationType,
    frozen: set[str],
) -> tuple[Workflow, MutationRecord] | None:
    """Attempt a single mutation of the given type."""
    mutable_nodes = [
        nid for nid in workflow.nodes if nid not in frozen and nid != workflow.start_node
    ]
    if not mutable_nodes and op != MutationType.NODE_INSERT:
        return None

    if op == MutationType.NODE_INSERT:
        target = random.choice(list(workflow.nodes.keys()))
        new_id = f"agent_{random.randint(100, 999)}"

        target_node = workflow.nodes.get(target)
        outgoing = [e.target for e in workflow.edges if e.source == target]
        next_node = workflow.nodes.get(outgoing[0]) if outgoing else None

        next_is_builder = (
            next_node is not None
            and hasattr(next_node, "role")
            and next_node.role == AgentRole.BUILDER  # type: ignore[union-attr]
        )
        target_is_builder = (
            target_node is not None
            and hasattr(target_node, "role")
            and target_node.role == AgentRole.BUILDER  # type: ignore[union-attr]
        )

        if next_is_builder:
            role = AgentRole.RESEARCHER
        elif target_is_builder:
            role = AgentRole.HEALTH_CHECKER
        else:
            role = random.choice([AgentRole.RESEARCHER, AgentRole.BUILDER, AgentRole.HEALTH_CHECKER])

        prompt = populate_prompt(role.value, "featurebench")

        new_node = AgentNode(
            id=new_id,
            role=role,
            prompt_template=prompt,
        )
        return insert_node(workflow, new_node, target, frozen_nodes=frozen)

    elif op == MutationType.NODE_REMOVE:
        target = random.choice(mutable_nodes)
        return remove_node(workflow, target, frozen_nodes=frozen)

    elif op == MutationType.EDGE_REDIRECT:
        edges_from_mutable = [
            e for e in workflow.edges if e.source not in frozen
        ]
        if not edges_from_mutable:
            return None
        edge = random.choice(edges_from_mutable)
        possible_targets = [nid for nid in workflow.nodes if nid != edge.target]
        if not possible_targets:
            return None
        new_target = random.choice(possible_targets)
        return redirect_edge(workflow, edge.source, edge.target, new_target, frozen_nodes=frozen)

    elif op == MutationType.PARALLELIZE:
        if len(mutable_nodes) < 2:
            return None
        pair = random.sample(mutable_nodes, 2)
        return parallelize(workflow, pair, frozen_nodes=frozen)

    elif op == MutationType.SERIALIZE:
        fork_ids = [
            nid for nid, n in workflow.nodes.items()
            if type(n).__name__ == "ForkNode" and nid not in frozen
        ]
        if not fork_ids:
            return None
        return serialize(workflow, random.choice(fork_ids), frozen_nodes=frozen)

    elif op == MutationType.PARAM_MUTATE:
        agent_nodes = [
            nid for nid in mutable_nodes
            if type(workflow.nodes[nid]).__name__ == "AgentNode"
        ]
        if not agent_nodes:
            return None
        target = random.choice(agent_nodes)
        param = random.choice(["timeout", "model"])
        if param == "timeout":
            changes: dict[str, object] = {"timeout": random.choice([300, 600, 900, 1200, 1800])}
        else:
            changes = {"model": random.choice(["sonnet", "opus", "haiku"])}
        return mutate_params(workflow, target, changes, frozen_nodes=frozen)

    elif op == MutationType.PROMPT_MUTATE:
        agent_nodes = [
            nid for nid in workflow.nodes
            if type(workflow.nodes[nid]).__name__ == "AgentNode" and nid not in frozen
        ]
        if not agent_nodes:
            return None
        count = min(random.randint(1, 3), len(agent_nodes))
        targets = random.sample(agent_nodes, count)
        return prompt_mutate(workflow, targets, frozen_nodes=frozen)

    return None


def prompt_mutate(
    workflow: Workflow,
    target_node_ids: list[str],
    *,
    frozen_nodes: set[str] | None = None,
    archive_best_prompts: dict[str, str] | None = None,
) -> tuple[Workflow, MutationRecord] | None:
    """Mutate prompts on selected AgentNodes using EvoPrompt-style crossover.

    Combines the current prompt with a donor prompt (from archive or template),
    preserving frozen segments (MUST/MUST NOT/FORBIDDEN/NEVER).
    """
    frozen = frozen_nodes or set()
    wf = _deep_copy_workflow(workflow)
    mutated_nodes: list[str] = []

    for node_id in target_node_ids:
        if node_id in frozen or node_id not in wf.nodes:
            continue
        node = wf.nodes[node_id]
        if type(node).__name__ != "AgentNode":
            continue

        agent_node: AgentNode = node  # type: ignore[assignment]
        original_prompt = agent_node.prompt_template or ""
        role_name = agent_node.role.value

        donor_prompt = ""
        if archive_best_prompts and role_name in archive_best_prompts:
            donor_prompt = archive_best_prompts[role_name]
        else:
            donor_prompt = populate_prompt(role_name, "featurebench")

        frozen_segments = _extract_frozen_segments(original_prompt)

        new_prompt = _crossover_prompts(original_prompt, donor_prompt, role_name)

        if not _validate_length(new_prompt, original_prompt):
            continue

        if not _validate_frozen_segments(new_prompt, frozen_segments):
            for seg in frozen_segments:
                if seg not in new_prompt:
                    new_prompt = new_prompt.rstrip(". ") + ". " + seg
            if not _validate_frozen_segments(new_prompt, frozen_segments):
                continue

        wf.nodes[node_id] = agent_node.model_copy(  # type: ignore[assignment]
            update={"prompt_template": new_prompt}
        )
        mutated_nodes.append(node_id)

    if not mutated_nodes:
        return None

    result = validate_and_repair(wf)
    if result is None:
        return None

    record = MutationRecord(
        operator=MutationType.PROMPT_MUTATE,
        target_node=mutated_nodes[0] if len(mutated_nodes) == 1 else None,
        before={"nodes": mutated_nodes},
        after={"mutated_count": len(mutated_nodes)},
        rationale=f"Prompt mutation on {mutated_nodes}",
    )
    return result, record


def _extract_frozen_segments(prompt: str) -> list[str]:
    """Extract frozen segments (MUST, MUST NOT, FORBIDDEN, etc.) from a prompt."""
    segments: list[str] = []
    for pattern in _FROZEN_SEGMENT_PATTERNS:
        for match in pattern.finditer(prompt):
            start = max(0, prompt.rfind(".", 0, match.start()) + 1)
            end = prompt.find(".", match.end())
            if end == -1:
                end = len(prompt)
            else:
                end += 1
            segment = prompt[start:end].strip()
            if segment and segment not in segments:
                segments.append(segment)
    return segments


def _crossover_prompts(current: str, donor: str, role: str) -> str:
    """EvoPrompt-style crossover: combine ideas from current and donor prompts."""
    if not current:
        return donor
    if not donor:
        return current

    current_sentences = [s.strip() for s in current.split(".") if s.strip()]
    donor_sentences = [s.strip() for s in donor.split(".") if s.strip()]

    result_sentences: list[str] = []

    max_len = max(len(current_sentences), len(donor_sentences))
    for i in range(max_len):
        if i < len(current_sentences) and i < len(donor_sentences):
            if random.random() < 0.5:
                result_sentences.append(current_sentences[i])
            else:
                result_sentences.append(donor_sentences[i])
        elif i < len(current_sentences):
            result_sentences.append(current_sentences[i])
        else:
            result_sentences.append(donor_sentences[i])

    return ". ".join(result_sentences) + "."


def _validate_length(new_prompt: str, original: str) -> bool:
    """Check mutated prompt is within acceptable length range of original.

    Short prompts (<100 chars) use a relaxed lower bound (50%) so crossover
    with longer donor templates can succeed.
    """
    if not original:
        return bool(new_prompt)
    orig_len = len(original)
    new_len = len(new_prompt)
    lower_bound = 0.5 if orig_len < 100 else 0.8
    return lower_bound * orig_len <= new_len <= 1.2 * orig_len


def _validate_frozen_segments(prompt: str, frozen_segments: list[str]) -> bool:
    """Verify all frozen segments survive in the mutated prompt."""
    return all(seg in prompt for seg in frozen_segments)
