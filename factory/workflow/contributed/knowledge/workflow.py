"""Knowledge workflow — observe an external agent, extract triplets, generate insights.

7-node pipeline: observe → extract_deterministic → extract_llm → update_graph
                 → analyst → gate_insights → report
RELOOP from gate_insights back to observe (max 3 iterations) when insights are thin.
"""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.contributed.knowledge.nodes import (
    make_analyst_node,
    make_extract_deterministic_node,
    make_extract_llm_node,
    make_gate_insights_node,
    make_observe_node,
    make_report_node,
    make_update_graph_node,
)
from factory.workflow.primitives import (
    Edge,
    VerdictType,
    Workflow,
)

meta = {
    "name": "knowledge",
    "description": (
        "Knowledge mode — observe an external agent, extract knowledge graph "
        "triplets from its execution traces, and generate insights about failure "
        "patterns, causal chains, and improvement opportunities. "
        "observe → extract → update_graph → analyst → gate_insights → report "
        "with RELOOP on insufficient insights."
    ),
}


def workflow() -> Workflow:
    """Build the knowledge workflow from composable node factories."""
    nodes: dict[str, Any] = {
        "observe": make_observe_node(),
        "extract_deterministic": make_extract_deterministic_node(),
        "extract_llm": make_extract_llm_node(),
        "update_graph": make_update_graph_node(),
        "analyst": make_analyst_node(),
        "gate_insights": make_gate_insights_node(),
        "report": make_report_node(),
    }

    edges = [
        Edge(source="observe", target="extract_deterministic"),
        Edge(source="extract_deterministic", target="extract_llm"),
        Edge(source="extract_llm", target="update_graph"),
        Edge(source="update_graph", target="analyst"),
        Edge(source="analyst", target="gate_insights"),
        Edge(source="gate_insights", target="report", condition=VerdictType.PROCEED),
        Edge(source="gate_insights", target="observe", condition=VerdictType.RELOOP),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "knowledge"

    return Workflow(
        name="knowledge",
        nodes=nodes,
        edges=edges,
        start_node="observe",
        terminal=True,
        trigger=trigger,
    )
