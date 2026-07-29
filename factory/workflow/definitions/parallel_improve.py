"""W12: Parallel Improve Mode workflow definition."""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.definitions._shared import _deep_qa_subgraph
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    JoinNode,
    SelectionNode,
    Study,
    SubgraphForkNode,
    VerdictType,
    Workflow,
)


def parallel_improve_workflow() -> Workflow:
    """W12: Parallel Improve — study -> research -> strategy -> fork N experiments -> select best.

    Reuses the improve workflow's shared prefix (study -> research -> strategy),
    then forks N hypotheses into isolated git worktrees, runs the per-experiment
    subgraph concurrently (begin -> builder -> QA -> eval), joins at a barrier,
    selects the best result, and merges the winner.
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # -- Shared prefix (identical to improve) --

    nodes["study"] = Study(
        id="study",
        command="factory study {project_path}",
        writes={".factory/strategy/observations.md"},
    )

    nodes["researcher"] = AgentNode(
        id="researcher",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Deep research for the project. "
            "Read observations at .factory/strategy/observations.md. "
            "Analyze codebase structure, eval scores, and experiment history. "
            "Search the web for best practices relevant to weak dimensions. "
            "Check .factory/archive/ for prior knowledge. "
            "Write findings to .factory/strategy/research-local.md."
        ),
        reads={".factory/strategy/observations.md"},
        writes={".factory/strategy/research-local.md"},
    )

    nodes["gate_research"] = GateNode(
        id="gate_research",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Are observations grounded in data? Did web research surface useful patterns? "
            "Any blind spots in the analysis?"
        ),
        reads={".factory/strategy/research-local.md"},
    )

    nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Generate prioritized hypotheses for PARALLEL execution. "
            "Read the backlog at .factory/strategy/backlog.md — clear as many items as possible. "
            "Read Hypothesis Budget from observations for constraints. "
            "Read CEO research review at .factory/reviews/ceo-verdict-researcher.md. "
            "Generate MULTIPLE independent hypotheses that can run concurrently. "
            "Each hypothesis must target different files/areas to avoid merge conflicts. "
            "Tag backlog items with **Backlog item:** and new items with **New:**. "
            "Write to .factory/strategy/current.md with each hypothesis under a "
            "## Hypothesis N heading."
        ),
        reads={".factory/strategy/research-local.md", ".factory/strategy/observations.md"},
        writes={".factory/strategy/current.md"},
    )

    nodes["gate_strategy"] = GateNode(
        id="gate_strategy",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "HARD GATE for parallel experiments. Check: "
            "Are hypotheses independent (target different files/areas)? "
            "Would merge conflicts be unlikely? "
            "Each specific enough to implement? Scoped to one PR each? "
            "Expected eval impact realistic? Follows FEEC priority? "
            "Write PLAN APPROVED with approved hypotheses."
        ),
        reads={".factory/strategy/current.md"},
    )

    # -- Per-experiment subgraph (runs N times in parallel worktrees) --

    nodes["exp_begin"] = FnNode(
        id="exp_begin",
        command='factory begin {project_path} --hypothesis "$HYPOTHESIS"',
        writes={".factory/experiments/current_id"},
    )

    nodes["exp_builder"] = AgentNode(
        id="exp_builder",
        role=AgentRole.BUILDER,
        prompt_template=(
            "Implement the current hypothesis from .factory/strategy/current.md. "
            "Read CLAUDE.md and factory.md. Read the CEO strategy approval. "
            "Implement exactly what the hypothesis describes. Run tests. "
            "Commit changes."
        ),
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    nodes["exp_gate_build"] = GateNode(
        id="exp_gate_build",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Read builder output and diff. Does work match the hypothesis? "
            "No scope creep? Tests included? REDIRECT if off-scope."
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    dq_nodes, dq_edges = _deep_qa_subgraph(
        code_reviewer_extra=" This is a parallel experiment branch.",
        adversarial_extra=" This is a parallel experiment branch.",
    )
    # Namespace deep-QA nodes for the experiment subgraph
    exp_dq_nodes: dict[str, Any] = {}
    exp_dq_edges: list[Edge] = []
    dq_rename = {nid: f"exp_{nid}" for nid in dq_nodes}
    for nid, node in dq_nodes.items():
        new_id = dq_rename[nid]
        new_node = node.model_copy(update={"id": new_id})
        exp_dq_nodes[new_id] = new_node
    for edge in dq_edges:
        exp_dq_edges.append(
            Edge(
                source=dq_rename[edge.source],
                target=dq_rename[edge.target],
                condition=edge.condition,
            )
        )
    nodes.update(exp_dq_nodes)

    nodes["exp_gate_qa"] = GateNode(
        id="exp_gate_qa",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review QA results for this experiment branch. "
            "PROCEED if all checks pass. "
            "RELOOP to exp_builder (max 3 iterations) if issues found."
        ),
        reads={
            ".factory/reviews/health-check.md",
            ".factory/reviews/code-review.md",
            ".factory/reviews/adversarial-qa.md",
        },
    )

    nodes["exp_gate_precheck"] = GateNode(
        id="exp_gate_precheck",
        evaluator_type="fn",
        evaluator_command="factory precheck {project_path} --score-before 0 --score-after 0",
        reads={".factory/reviews/adversarial-qa.md"},
    )

    nodes["exp_eval"] = FnNode(
        id="exp_eval",
        command="factory eval {project_path}",
        reads={".factory/reviews/adversarial-qa.md"},
        writes={".factory/last_eval.json"},
    )

    # -- SubgraphForkNode: fork N experiment branches --

    nodes["fork_experiments"] = SubgraphForkNode(
        id="fork_experiments",
        subgraph_entry="exp_begin",
        subgraph_exit="exp_eval",
        parallelism=3,
        reads={".factory/strategy/current.md"},
        writes={".factory/parallel_results.json"},
    )

    # -- JoinNode: barrier after all branches --

    nodes["join_experiments"] = JoinNode(
        id="join_experiments",
        sources=["fork_experiments"],
        reads={".factory/parallel_results.json"},
        writes={".factory/parallel_joined.json"},
    )

    # -- SelectionNode: pick the best --

    nodes["select_best"] = SelectionNode(
        id="select_best",
        strategy="best_score",
        reads={".factory/parallel_joined.json"},
        writes={".factory/selection_result.json"},
    )

    # -- Post-selection --

    nodes["archivist"] = AgentNode(
        id="archivist",
        role=AgentRole.ARCHIVIST,
        prompt_template=(
            "Archive parallel experiment tournament results. "
            "Record which hypotheses were tested, their scores, "
            "which one won and why, and learnings from losers."
        ),
        reads={".factory/selection_result.json"},
        writes={".factory/archive/experiment.md"},
        blocking=False,
    )

    # -- Edges --

    # Shared prefix
    edges = [
        Edge(source="study", target="researcher"),
        Edge(source="researcher", target="gate_research"),
        Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED),
        Edge(source="gate_research", target="researcher", condition=VerdictType.RELOOP),
        Edge(source="strategist", target="gate_strategy"),
        Edge(source="gate_strategy", target="fork_experiments", condition=VerdictType.PROCEED),
        Edge(source="gate_strategy", target="strategist", condition=VerdictType.RELOOP),
    ]

    # Per-experiment subgraph edges
    edges.extend(
        [
            Edge(source="exp_begin", target="exp_builder"),
            Edge(source="exp_builder", target="exp_gate_build"),
            Edge(
                source="exp_gate_build", target="exp_health_checker", condition=VerdictType.PROCEED
            ),
            Edge(source="exp_gate_build", target="exp_builder", condition=VerdictType.RELOOP),
            *exp_dq_edges,
            Edge(source="exp_adversarial_tester", target="exp_gate_qa"),
            Edge(source="exp_gate_qa", target="exp_gate_precheck", condition=VerdictType.PROCEED),
            Edge(source="exp_gate_qa", target="exp_builder", condition=VerdictType.RELOOP),
            Edge(source="exp_gate_precheck", target="exp_eval", condition=VerdictType.PROCEED),
            Edge(source="exp_gate_precheck", target="exp_eval", condition=VerdictType.HALT),
        ]
    )

    # Fork -> Join -> Select -> Archive
    edges.extend(
        [
            Edge(source="fork_experiments", target="join_experiments"),
            Edge(source="join_experiments", target="select_best"),
            Edge(source="select_best", target="archivist"),
        ]
    )

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state == ProjectState.HAS_FACTORY and ctx.get("mode") == "parallel-improve"

    return Workflow(
        name="parallel-improve",
        nodes=nodes,
        edges=edges,
        start_node="study",
        trigger=trigger,
    )
