"""Designer Agent — dual-mode workflow designer and informed mutation proposer.

Design mode: creates from-scratch workflow designs (minimal, thorough, custom).
Mutation mode: proposes targeted mutations based on failure telemetry.

v1 uses deterministic templates. LLM integration comes when the outer loop
runs against real benchmarks.
"""

from __future__ import annotations

import structlog

from factory.outer_loop.models import EvalResult, MutationRecord, MutationType
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    DataNode,
    Edge,
    FnNode,
    GateNode,
    Workflow,
)

log = structlog.get_logger()


class DesignerAgent:
    """LLM-guided workflow designer with design and mutation modes.

    Design mode produces from-scratch workflows for seed diversity.
    Mutation mode proposes targeted mutations from failure telemetry.
    """

    def design_minimal(
        self,
        benchmark_spec: str,
        seed_workflow: Workflow | None = None,
        frozen_node_ids: set[str] | None = None,
    ) -> Workflow:
        """Create a 3-4 node workflow optimized for speed.

        Structure: researcher → builder → gate
        """
        nodes: dict[str, AgentNode | FnNode | GateNode] = {
            "researcher": AgentNode(
                id="researcher",
                role=AgentRole.RESEARCHER,
                writes={".factory/strategy/research.md"},
                timeout=300,
            ),
            "builder": AgentNode(
                id="builder",
                role=AgentRole.BUILDER,
                reads={".factory/strategy/research.md"},
                writes={".factory/reviews/builder-latest.md"},
                timeout=600,
            ),
            "gate_qa": GateNode(
                id="gate_qa",
                evaluator_type="agent",
                evaluator_role=AgentRole.HEALTH_CHECKER,
                reads={".factory/reviews/builder-latest.md"},
            ),
        }

        _inject_frozen_nodes(nodes, seed_workflow, frozen_node_ids)

        edges = [
            Edge(source="researcher", target="builder"),
            Edge(source="builder", target="gate_qa"),
        ]

        start_node = "researcher"
        new_start = _rewire_data_nodes(
            nodes, edges, start_node, seed_workflow, frozen_node_ids
        )
        if new_start is not None:
            start_node = new_start

        wf = Workflow(
            name=f"minimal_{_slug(benchmark_spec)}",
            nodes=nodes,  # type: ignore[arg-type]
            edges=edges,
            start_node=start_node,
        )
        log.info("designed_minimal", nodes=len(wf.nodes), benchmark=benchmark_spec[:40])
        return wf

    def design_thorough(
        self,
        benchmark_spec: str,
        seed_workflow: Workflow | None = None,
        frozen_node_ids: set[str] | None = None,
    ) -> Workflow:
        """Create an 8-10 node workflow optimized for thoroughness.

        Structure: study → researcher → strategist → fork(builder_a, builder_b)
                   → join → code_reviewer → adversarial_tester → gate
        """
        from factory.workflow.primitives import ForkNode, JoinNode

        nodes: dict[str, AgentNode | FnNode | GateNode | ForkNode | JoinNode] = {
            "study": FnNode(
                id="study",
                command="factory study {project_path}",
                writes={".factory/strategy/observations.md"},
            ),
            "researcher": AgentNode(
                id="researcher",
                role=AgentRole.RESEARCHER,
                reads={".factory/strategy/observations.md"},
                writes={".factory/strategy/research.md"},
                timeout=600,
            ),
            "strategist": AgentNode(
                id="strategist",
                role=AgentRole.STRATEGIST,
                reads={".factory/strategy/research.md"},
                writes={".factory/strategy/current.md"},
                timeout=600,
            ),
            "fork_builders": ForkNode(
                id="fork_builders",
                targets=["builder_a", "builder_b"],
                reads={".factory/strategy/current.md"},
            ),
            "builder_a": AgentNode(
                id="builder_a",
                role=AgentRole.BUILDER,
                reads={".factory/strategy/current.md"},
                writes={".factory/reviews/builder-a.md"},
                timeout=1200,
            ),
            "builder_b": AgentNode(
                id="builder_b",
                role=AgentRole.BUILDER,
                reads={".factory/strategy/current.md"},
                writes={".factory/reviews/builder-b.md"},
                timeout=1200,
            ),
            "join_builders": JoinNode(
                id="join_builders",
                sources=["builder_a", "builder_b"],
            ),
            "code_reviewer": AgentNode(
                id="code_reviewer",
                role=AgentRole.CODE_REVIEWER,
                reads={".factory/reviews/builder-a.md", ".factory/reviews/builder-b.md"},
                writes={".factory/reviews/code-review.md"},
                timeout=900,
            ),
            "adversarial_tester": AgentNode(
                id="adversarial_tester",
                role=AgentRole.ADVERSARIAL_TESTER,
                reads={".factory/reviews/code-review.md"},
                writes={".factory/reviews/adversarial-qa.md"},
                timeout=1800,
            ),
            "gate_qa": GateNode(
                id="gate_qa",
                evaluator_type="agent",
                evaluator_role=AgentRole.CEO,
                reads={".factory/reviews/adversarial-qa.md"},
            ),
        }

        _inject_frozen_nodes(nodes, seed_workflow, frozen_node_ids)

        edges = [
            Edge(source="study", target="researcher"),
            Edge(source="researcher", target="strategist"),
            Edge(source="strategist", target="fork_builders"),
            Edge(source="fork_builders", target="builder_a"),
            Edge(source="fork_builders", target="builder_b"),
            Edge(source="builder_a", target="join_builders"),
            Edge(source="builder_b", target="join_builders"),
            Edge(source="join_builders", target="code_reviewer"),
            Edge(source="code_reviewer", target="adversarial_tester"),
            Edge(source="adversarial_tester", target="gate_qa"),
        ]

        start_node = "study"
        new_start = _rewire_data_nodes(
            nodes, edges, start_node, seed_workflow, frozen_node_ids
        )
        if new_start is not None:
            start_node = new_start

        wf = Workflow(
            name=f"thorough_{_slug(benchmark_spec)}",
            nodes=nodes,  # type: ignore[arg-type]
            edges=edges,
            start_node=start_node,
        )
        log.info("designed_thorough", nodes=len(wf.nodes), benchmark=benchmark_spec[:40])
        return wf

    def design_custom(
        self,
        benchmark_spec: str,
        constraints: dict[str, object],
        seed_workflow: Workflow | None = None,
        frozen_node_ids: set[str] | None = None,
    ) -> Workflow:
        """Create a custom from-scratch workflow with optional constraints.

        Constraints can specify:
        - max_nodes: int — cap on node count
        - require_roles: list[str] — roles that must be present
        - parallel: bool — whether to include fork/join parallelism
        """
        raw_max = constraints.get("max_nodes", 6)
        max_nodes = int(raw_max) if isinstance(raw_max, (int, float, str)) else 6
        raw_roles = constraints.get("require_roles", [])
        require_roles: list[object] = list(raw_roles) if isinstance(raw_roles, list) else []

        nodes: dict[str, AgentNode | FnNode | GateNode] = {}
        edges: list[Edge] = []
        prev_id: str | None = None

        core_roles: list[tuple[str, AgentRole]] = [
            ("researcher", AgentRole.RESEARCHER),
            ("strategist", AgentRole.STRATEGIST),
            ("builder", AgentRole.BUILDER),
        ]

        for role_str in require_roles:
            if isinstance(role_str, str) and not any(r[0] == role_str for r in core_roles):
                try:
                    role_enum = AgentRole(role_str)
                    core_roles.append((role_str, role_enum))
                except ValueError:
                    pass

        node_budget = max_nodes - 1
        for node_id, role in core_roles:
            if len(nodes) >= node_budget:
                break
            nodes[node_id] = AgentNode(
                id=node_id,
                role=role,
                timeout=600,
            )
            if prev_id is not None:
                edges.append(Edge(source=prev_id, target=node_id))
            prev_id = node_id

        if prev_id is not None:
            gate_id = "gate_qa"
            nodes[gate_id] = GateNode(  # type: ignore[assignment]
                id=gate_id,
                evaluator_type="agent",
                evaluator_role=AgentRole.HEALTH_CHECKER,
            )
            edges.append(Edge(source=prev_id, target=gate_id))

        _inject_frozen_nodes(nodes, seed_workflow, frozen_node_ids)

        start = core_roles[0][0] if core_roles else "gate_qa"
        new_start = _rewire_data_nodes(
            nodes, edges, start, seed_workflow, frozen_node_ids
        )
        if new_start is not None:
            start = new_start

        wf = Workflow(
            name=f"custom_{_slug(benchmark_spec)}",
            nodes=nodes,  # type: ignore[arg-type]
            edges=edges,
            start_node=start,
        )
        log.info("designed_custom", nodes=len(wf.nodes), benchmark=benchmark_spec[:40])
        return wf

    def propose(
        self,
        parent_workflow: Workflow,
        telemetry: dict[str, object],
        archive_stats: dict[str, object],
        benchmark_spec: str,
    ) -> list[MutationRecord]:
        """Propose 1-3 targeted mutations based on failure telemetry.

        Heuristics:
        - High failure rate on a node → propose removing or replacing it
        - Dominant failure is timeout → propose reducing parallelism or increasing timeout
        - Low diversity → propose inserting a new agent role not yet present
        """
        proposals: list[MutationRecord] = []

        node_stats = telemetry.get("node_stats", {})
        if isinstance(node_stats, dict):
            for node_id, stats in node_stats.items():
                if not isinstance(stats, dict):
                    continue
                failure_rate = stats.get("failure_rate", 0.0)
                if isinstance(failure_rate, (int, float)) and failure_rate > 0.5:
                    proposals.append(MutationRecord(
                        operator=MutationType.NODE_REMOVE,
                        target_node=node_id,
                        before={"failure_rate": failure_rate},
                        after={"action": "remove_failing_node"},
                        rationale=f"Node {node_id} has {failure_rate:.0%} failure rate",
                    ))

        dominant_failure = telemetry.get("dominant_failure", "")
        if dominant_failure == "timeout":
            agent_nodes = [
                nid for nid, node in parent_workflow.nodes.items()
                if type(node).__name__ == "AgentNode"
            ]
            if agent_nodes:
                target = agent_nodes[0]
                current_timeout = getattr(parent_workflow.nodes[target], "timeout", 600)
                new_timeout = min((current_timeout or 600) * 2, 3600)
                proposals.append(MutationRecord(
                    operator=MutationType.PARAM_MUTATE,
                    target_node=target,
                    before={"timeout": current_timeout},
                    after={"timeout": new_timeout},
                    rationale="Dominant failure is timeout — increase timeout",
                ))

        diversity = archive_stats.get("diversity", 1.0)
        if isinstance(diversity, (int, float)) and diversity < 0.3:
            present_roles = {
                node.role.value  # type: ignore[union-attr]
                for node in parent_workflow.nodes.values()
                if hasattr(node, "role")
            }
            missing = set(AgentRole) - {AgentRole(r) for r in present_roles if r in [ar.value for ar in AgentRole]}
            if missing:
                new_role = next(iter(missing))
                proposals.append(MutationRecord(
                    operator=MutationType.NODE_INSERT,
                    target_node=None,
                    before={"present_roles": sorted(present_roles)},
                    after={"new_role": new_role.value},
                    rationale=f"Low diversity ({diversity:.2f}) — insert {new_role.value}",
                ))

        if not proposals:
            proposals.append(MutationRecord(
                operator=MutationType.PARAM_MUTATE,
                target_node=None,
                before={},
                after={"action": "explore"},
                rationale="No specific failure signal — propose parameter exploration",
            ))

        return proposals[:3]


def _inject_frozen_nodes(
    nodes: dict[str, object],
    seed_workflow: Workflow | None,
    frozen_node_ids: set[str] | None,
) -> None:
    """Inject frozen nodes from a seed workflow into a template nodes dict.

    Frozen nodes take precedence over template nodes on ID collision.
    """
    if not seed_workflow or not frozen_node_ids:
        return
    for frozen_id in frozen_node_ids:
        if frozen_id in seed_workflow.nodes:
            if frozen_id in nodes:
                log.warning(
                    "frozen_node_collision",
                    node_id=frozen_id,
                    action="preferring_frozen_over_template",
                )
            nodes[frozen_id] = seed_workflow.nodes[frozen_id]
        else:
            log.warning("frozen_node_missing_in_seed", node_id=frozen_id)


def _rewire_data_nodes(
    nodes: dict[str, object],
    edges: list[Edge],
    original_start: str,
    seed_workflow: Workflow | None,
    frozen_node_ids: set[str] | None,
) -> str | None:
    """Rewire injected frozen DataNodes so they integrate into the template.

    For each frozen DataNode:
    1. Update subgraph_entry → template's original start_node
    2. Update subgraph_exit  → template's terminal node (no outgoing edges)

    No explicit edge is added from the DataNode to subgraph_entry — the
    executor reads subgraph_entry directly from the DataNode object.
    Adding an explicit edge would fail validation (_validate_datanode_edges
    rejects edges from a DataNode to its own subgraph nodes).

    Returns the DataNode ID (new start_node) or None if no DataNode was injected.
    """
    if not seed_workflow or not frozen_node_ids:
        return None

    # Find terminal node: the node with no outgoing edges (among template edges)
    sources = {e.source for e in edges}
    all_node_ids = set(nodes.keys())
    terminal_candidates = all_node_ids - sources
    # Exclude the frozen DataNodes themselves from terminal candidates
    frozen_data_ids: set[str] = set()

    for fid in frozen_node_ids:
        node = nodes.get(fid)
        if isinstance(node, DataNode):
            frozen_data_ids.add(fid)

    if not frozen_data_ids:
        return None

    terminal_candidates -= frozen_data_ids
    terminal_node = next(iter(terminal_candidates)) if terminal_candidates else original_start

    new_start: str | None = None
    for data_id in frozen_data_ids:
        data_node = nodes[data_id]
        assert isinstance(data_node, DataNode)

        # Determine subgraph entry: if the DataNode ID collides with the
        # template's original_start, follow edges to find the actual first
        # template node (otherwise subgraph_entry would point to itself).
        # Also remove the now-stale edges from original_start — they would
        # become invalid edges from the DataNode to its own subgraph.
        entry = original_start
        if data_id == original_start:
            for edge in edges:
                if edge.source == original_start:
                    entry = edge.target
                    break
            edges[:] = [e for e in edges if e.source != original_start]

        # Replace with updated subgraph_entry/exit pointing to template nodes
        updated = data_node.model_copy(
            update={"subgraph_entry": entry, "subgraph_exit": terminal_node}
        )
        nodes[data_id] = updated
        new_start = data_id

    return new_start


def extract_telemetry(eval_result: EvalResult) -> dict[str, object]:
    """Extract structured diagnostics from an EvalResult.

    Returns a dict with:
    - node_stats: per-node success/failure data (from details if available)
    - dominant_failure: most common failure category
    - benchmark_score: the raw benchmark score
    - cost_usd: evaluation cost
    - complexity: workflow complexity metric
    """
    details = eval_result.details or {}

    node_stats: dict[str, object] = {}
    raw_stats = details.get("node_stats", {})
    if isinstance(raw_stats, dict):
        node_stats = dict(raw_stats)

    dominant_failure = ""
    raw_failure = details.get("dominant_failure", "")
    if isinstance(raw_failure, str):
        dominant_failure = raw_failure

    return {
        "node_stats": node_stats,
        "dominant_failure": dominant_failure,
        "benchmark_score": eval_result.benchmark_score,
        "hygiene_score": eval_result.hygiene_score,
        "cost_usd": eval_result.cost_usd,
        "complexity": eval_result.complexity,
        "score": eval_result.score,
    }


def _slug(text: str) -> str:
    """Convert text to a short slug for workflow naming."""
    clean = text.lower().replace(" ", "_")[:20]
    return "".join(c for c in clean if c.isalnum() or c == "_").strip("_") or "default"
