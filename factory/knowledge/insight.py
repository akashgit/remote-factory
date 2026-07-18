"""Insight model and rendering — the actual detection is agent-driven."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from factory.knowledge.models import KnowledgeGraph


class InsightType(str, Enum):
    """Categories of insights the agent can produce."""

    FAILURE_PATTERN = "failure_pattern"
    MISSING_KNOWLEDGE = "missing_knowledge"
    CONTRADICTION = "contradiction"
    IMPROVEMENT_OPPORTUNITY = "improvement_opportunity"
    CAUSAL_CHAIN = "causal_chain"


class Insight(BaseModel):
    """A single insight produced by an agent exploring the knowledge graph."""

    model_config = ConfigDict(strict=True, extra="forbid")

    type: InsightType
    title: str
    description: str
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    evidence_triplet_ids: list[str] = []
    causal_path: list[str] = []
    suggested_action: str = ""


def format_insights(insights: list[Insight], graph: KnowledgeGraph) -> str:
    """Render insights as markdown with evidence from the graph."""
    if not insights:
        return "No insights generated."

    triplet_index = {t.id: t for t in graph.triplets}
    lines = [
        f"# Insights for {graph.task_id}",
        "",
        f"**{len(insights)} insight(s)** from {graph.triplet_count()} triplets",
        "",
    ]

    for i, insight in enumerate(insights, 1):
        lines.append(f"## {i}. [{insight.type.value}] {insight.title}")
        lines.append("")
        lines.append(insight.description)
        lines.append("")

        if insight.confidence < 1.0:
            lines.append(f"**Confidence:** {insight.confidence:.0%}")
            lines.append("")

        if insight.suggested_action:
            lines.append(f"**Suggested action:** {insight.suggested_action}")
            lines.append("")

        if insight.causal_path:
            lines.append("**Causal path:** " + " → ".join(insight.causal_path))
            lines.append("")

        if insight.evidence_triplet_ids:
            lines.append("**Evidence:**")
            for tid in insight.evidence_triplet_ids:
                t = triplet_index.get(tid)
                if t:
                    lines.append(f"- ({t.subject.name}, {t.predicate.value}, {t.object.name})")
                else:
                    lines.append(f"- [triplet {tid} not found]")
            lines.append("")

    return "\n".join(lines)
