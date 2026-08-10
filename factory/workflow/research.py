"""Research-standalone parallel research workflow.

Runs the decomposed research pipeline (fork → 3 researchers → join → gate)
as a standalone mode. Triggered via `factory workflow run research-standalone`
or `factory ceo /path --mode research-standalone`.
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.definitions import ResearcherConfig, _research_subgraph
from factory.workflow.primitives import AgentNode, Edge, Workflow

meta = {
    "name": "research-standalone",
    "description": (
        "Standalone parallel research pipeline — 3 researcher agents "
        "(similar, techstack, pitfalls) forked in parallel, joined at a "
        "barrier, then gated by the CEO for quality."
    ),
}


def workflow() -> Workflow:
    """Build the standalone research workflow."""
    _DEFAULT_RESEARCHERS = [
        ResearcherConfig(
            id="similar",
            prompt_template=(
                "Similar projects research. "
                "Search the web for similar projects, existing solutions, and prior art. "
                "Analyze their strengths, weaknesses, and market positioning. "
                "Check .factory/archive/ for prior knowledge on similar builds. "
                "Write findings to .factory/strategy/research-similar.md covering: "
                "similar projects found (with links), what they do well and what's missing, "
                "differentiation opportunities."
            ),
            post_check_min_size=50,
        ),
        ResearcherConfig(
            id="techstack",
            prompt_template=(
                "Tech stack research. "
                "Identify the best technology stack for this type of project. "
                "Find architecture patterns and best practices. "
                "Evaluate framework/library options with trade-offs. "
                "Write findings to .factory/strategy/research-techstack.md covering: "
                "recommended tech stack with rationale, architecture patterns, "
                "framework comparisons."
            ),
            post_check_min_size=50,
        ),
        ResearcherConfig(
            id="pitfalls",
            prompt_template=(
                "Pitfalls and scope research. "
                "Identify potential pitfalls and common mistakes for this type of project. "
                "Research MVP scope best practices. "
                "Check .factory/archive/ for lessons from past builds. "
                "Write findings to .factory/strategy/research-pitfalls.md covering: "
                "potential pitfalls to avoid, MVP scope recommendation, "
                "lessons from similar past builds."
            ),
            post_check_min_size=50,
        ),
    ]

    r_nodes, r_edges = _research_subgraph(
        researchers=_DEFAULT_RESEARCHERS,
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
    edges: list[Edge] = [*r_edges]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "research-standalone"

    return Workflow(
        name="research-standalone",
        nodes=nodes,
        edges=edges,
        start_node="fork_research",
        trigger=trigger,
    )
