"""Strategy-standalone workflow.

Runs the decomposed strategy pipeline (strategist → CEO gate) as a
standalone mode, looping back to the strategist on RELOOP verdicts until
the gate passes. Triggered via `factory workflow run strategy-standalone`
or `factory ceo /path --mode strategy-standalone`.
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.definitions import StrategyConfig, _strategy_subgraph
from factory.workflow.primitives import AgentNode, ArtifactCheck, Edge, VerdictType, Workflow

meta = {
    "name": "strategy-standalone",
    "description": (
        "Standalone strategy pipeline — the Strategist synthesizes a "
        "buildable phased plan into .factory/strategy/current.md, then a "
        "CEO HARD GATE reviews it. RELOOP verdicts return to the "
        "strategist (max 3 iterations)."
    ),
}


def workflow() -> Workflow:
    """Build the standalone strategy workflow."""
    s_nodes, s_edges = _strategy_subgraph(
        config=StrategyConfig(
            prompt_template=(
                "Synthesize a project specification from research. "
                "Read ALL tagged research files at .factory/strategy/research-*.md. "
                "Produce a complete phased build plan. Phase 1 must be project scaffold + eval harness. "
                "Every Phase must have substantive What/Why/Expected impact fields. "
                "Build EVERYTHING in this pass. Only defer items requiring human intervention. "
                "Write the plan to .factory/strategy/current.md."
            ),
            reads=frozenset({".factory/strategy/research-combined.md"}),
            post_checks=(
                ArtifactCheck(
                    path=".factory/strategy/current.md",
                    must_exist=True,
                    min_size=200,
                    must_contain=["### Phase 1", "### Architecture"],
                ),
            ),
            gate_prompt=(
                "HARD GATE — Builder MUST NOT start until approved. Check: "
                "1) Depth: every hypothesis has Category/What/Why/Expected impact. "
                "2) Research grounding: architecture and rationale cite research findings. "
                "3) Buildability: a Builder could implement each phase without clarifying questions. "
                "4) Phase 1 is scaffold + eval harness. "
                "5) Deferred section only contains items requiring human intervention. "
                "Write PLAN APPROVED in verdict if all checks pass."
            ),
        ),
    )

    strategist = s_nodes["strategist"]
    assert isinstance(strategist, AgentNode)
    s_nodes["strategist"] = strategist.model_copy(update={"reads": set()})

    nodes: dict[str, Any] = {**s_nodes}
    edges: list[Edge] = [
        *s_edges,
        # RELOOP loops back to the strategist so a failing gate re-plans
        # instead of halting (max 3 iterations).
        Edge(
            source="gate_strategy",
            target="strategist",
            condition=VerdictType.RELOOP,
        ),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "strategy-standalone"

    return Workflow(
        name="strategy-standalone",
        nodes=nodes,
        edges=edges,
        start_node="strategist",
        trigger=trigger,
    )
