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


tau_meta = {
    "name": "knowledge-tau",
    "description": (
        "Tau-bench knowledge mode — closed-loop improvement cycle. "
        "run_eval → extract → analyze → gate_insights → gate_score → "
        "improve → re_eval → gate_compare → report. "
        "Extracts structured triplets from tau-bench simulation JSON "
        "and uses insights to improve the agent."
    ),
}


def tau_workflow() -> Workflow:
    """Build the tau-bench evaluation + improvement workflow."""
    from factory.workflow.contributed.knowledge.nodes import (
        make_extract_tau_node,
        make_gate_compare_node,
        make_gate_score_node,
        make_improve_node,
        make_re_eval_node,
        make_run_eval_node,
    )

    extract_llm = make_extract_llm_node()
    extract_llm.reads = {".factory/knowledge/simulation.json"}

    nodes: dict[str, Any] = {
        "run_eval": make_run_eval_node(),
        "extract_tau": make_extract_tau_node(),
        "extract_llm": extract_llm,
        "update_graph": make_update_graph_node(),
        "analyst": make_analyst_node(),
        "gate_insights": make_gate_insights_node(),
        "gate_score": make_gate_score_node(),
        "improve": make_improve_node(),
        "re_eval": make_re_eval_node(),
        "gate_compare": make_gate_compare_node(),
        "report": make_report_node(),
    }

    edges = [
        Edge(source="run_eval", target="extract_tau"),
        Edge(source="extract_tau", target="extract_llm"),
        Edge(source="extract_llm", target="update_graph"),
        Edge(source="update_graph", target="analyst"),
        Edge(source="analyst", target="gate_insights"),
        Edge(source="gate_insights", target="gate_score", condition=VerdictType.PROCEED),
        Edge(source="gate_insights", target="run_eval", condition=VerdictType.RELOOP),
        Edge(source="gate_score", target="report", condition=VerdictType.PROCEED),
        Edge(source="gate_score", target="improve", condition=VerdictType.RELOOP),
        Edge(source="improve", target="re_eval"),
        Edge(source="re_eval", target="gate_compare"),
        Edge(source="gate_compare", target="report", condition=VerdictType.PROCEED),
        Edge(source="gate_compare", target="improve", condition=VerdictType.RELOOP),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "knowledge-tau"

    return Workflow(
        name="knowledge-tau",
        nodes=nodes,
        edges=edges,
        start_node="run_eval",
        terminal=True,
        trigger=trigger,
    )
