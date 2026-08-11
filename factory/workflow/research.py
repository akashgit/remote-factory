"""Research-standalone parallel research workflow.

Runs the decomposed research pipeline (fork → 3 researchers → join → gate)
as a standalone mode, looping back to the fork on RELOOP verdicts until
the CEO gate passes. Triggered via `factory workflow run research-standalone`
or `factory ceo /path --mode research-standalone`.
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.definitions import BUILD_RESEARCHERS, _research_subgraph
from factory.workflow.primitives import AgentNode, Edge, VerdictType, Workflow

meta = {
    "name": "research-standalone",
    "description": (
        "Standalone parallel research pipeline — 3 researcher agents "
        "(similar, techstack, pitfalls) forked in parallel, joined at a "
        "barrier, then gated by the CEO for quality. RELOOP verdicts "
        "return to the fork (max 3 iterations)."
    ),
}


def workflow() -> Workflow:
    """Build the standalone research workflow."""
    r_nodes, r_edges = _research_subgraph(
        researchers=BUILD_RESEARCHERS,
        gate_prompt=(
            "Is the research relevant? Does it cover the technology landscape adequately? "
            "Check for gaps in similar projects, tech stack analysis, and pitfall coverage."
        ),
    )

    for nid in ("researcher_similar", "researcher_techstack", "researcher_pitfalls"):
        node = r_nodes[nid]
        assert isinstance(node, AgentNode)
        r_nodes[nid] = node.model_copy(update={"reads": set()})

    nodes: dict[str, Any] = {**r_nodes}
    edges: list[Edge] = [
        *r_edges,
        # RELOOP loops back to the fork so the CEO gate can demand a
        # re-run of research instead of halting (max 3 iterations).
        Edge(
            source="gate_research",
            target="fork_research",
            condition=VerdictType.RELOOP,
        ),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "research-standalone"

    return Workflow(
        name="research-standalone",
        nodes=nodes,
        edges=edges,
        start_node="fork_research",
        trigger=trigger,
    )
