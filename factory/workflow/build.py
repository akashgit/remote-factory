"""Build-standalone workflow.

Runs the decomposed build pipeline (builder → CEO gate → deep-QA) as a
standalone mode.  This is the full "build factory": implementation plus
its own eval loop (health_checker → code_reviewer → gate_review →
adversarial_tester → gate_qa), RELOOPing back to the builder until the
QA gate passes (max 3 iterations).  Triggered via
`factory workflow run build-standalone` or
`factory ceo /path --mode build-standalone`.
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.definitions import BuildConfig, _build_subgraph, _deep_qa_subgraph
from factory.workflow.primitives import (
    AgentRole,
    ArtifactCheck,
    Edge,
    GateNode,
    VerdictType,
    Workflow,
)

meta = {
    "name": "build-standalone",
    "description": (
        "Standalone build factory — the Builder implements the current "
        "phase from .factory/strategy/current.md, commits, opens a draft PR, "
        "then the deep-QA eval loop (health checker, code reviewer, "
        "adversarial tester) verifies it. RELOOP verdicts return to the "
        "builder (max 3 iterations)."
    ),
}


def workflow() -> Workflow:
    """Build the standalone build workflow (build stage + deep-QA eval)."""
    b_nodes, b_edges = _build_subgraph(
        config=BuildConfig(
            prompt_template=(
                "Implement the next phase from .factory/strategy/current.md. "
                "Read the CEO's plan approval at .factory/reviews/ceo-verdict-strategist.md. "
                "Read CLAUDE.md and factory.md if they exist. "
                "Implement exactly what the current phase describes. Run tests. "
                "Commit changes and open a draft PR."
            ),
            reads=frozenset({".factory/strategy/current.md"}),
            post_checks=(
                ArtifactCheck(
                    path=".factory/reviews/builder-latest.md",
                    must_exist=True,
                    min_size=500,
                    must_contain=["commit"],
                ),
            ),
            gate_prompt=(
                "Read builder output. Check git log and diff. "
                "Does the work match the plan for this phase? "
                "If the Builder opened a PR, read it. "
                "REDIRECT if off-scope or missed key requirements."
            ),
        ),
    )

    dq_nodes, dq_edges = _deep_qa_subgraph()
    nodes: dict[str, Any] = {**b_nodes, **dq_nodes}

    # Gate the deep-QA output with a CEO gate, RELOOPing to the builder.
    nodes["gate_qa"] = GateNode(
        id="gate_qa",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review QA results. PROCEED if all checks pass. "
            "RELOOP to builder (max 3 iterations) if issues found."
        ),
        reads={
            ".factory/reviews/health-check.md",
            ".factory/reviews/code-review.md",
            ".factory/reviews/adversarial-qa.md",
        },
    )

    # Standalone boundary: no predecessors exist, so clear reads on every
    # node (validation requires reads ⊆ predecessor writes).
    for nid in (
        "builder",
        "health_checker",
        "code_reviewer",
        "adversarial_tester",
        "gate_qa",
    ):
        nodes[nid] = nodes[nid].model_copy(update={"reads": set()})

    edges: list[Edge] = [
        *b_edges,
        Edge(source="gate_build", target="health_checker", condition=VerdictType.PROCEED),
        Edge(source="gate_build", target="builder", condition=VerdictType.RELOOP),
        *dq_edges,
        Edge(source="adversarial_tester", target="gate_qa"),
        Edge(source="gate_qa", target="builder", condition=VerdictType.RELOOP),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "build-standalone"

    return Workflow(
        name="build-standalone",
        nodes=nodes,
        edges=edges,
        start_node="builder",
        trigger=trigger,
    )
