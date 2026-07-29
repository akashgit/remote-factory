"""W10: Skill Refine workflow definition."""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)


def skill_refine_workflow() -> Workflow:
    """W10: Verified skill generation pipeline.

    dag_sort -> templatize -> review_agent -> guard(RELOOP -> review_agent, max 2) ->
    split -> SKILL.md + SKILL.annotations.yaml

    On 3rd guard failure, falls back to unrefined templatize output.
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    nodes["dag_sort"] = FnNode(
        id="dag_sort",
        command="factory workflow show {project_path}",
        notes="Dump the workflow DAG in topological order. Must run first to provide node ordering for templatization.",
        writes={".factory/strategy/dag-order.md"},
    )

    nodes["templatize"] = FnNode(
        id="templatize",
        command="factory workflow export-skills --templatize {project_path}",
        notes="Convert the workflow graph into a templatized SKILL.md with slot markers for the reviewer to refine.",
        reads={".factory/strategy/dag-order.md"},
        writes={".factory/strategy/templatized-skill.md"},
    )

    nodes["review_agent"] = AgentNode(
        id="review_agent",
        role=AgentRole.SKILL_REVIEWER,
        model="opus",
        prompt_template=(
            "Review and refine the templatized skill document. "
            "You may ONLY modify values inside double-brace slot markers (format: name::default). "
            "Do NOT change any text outside markers, annotations, or structure. "
            "Use the provided context bundle (agent prompts, CLI docs, edge topology) "
            "to make informed improvements to timeouts, task prompts, gate prompts, "
            "failure actions, and finalize commands."
        ),
        reads={".factory/strategy/templatized-skill.md"},
        writes={".factory/strategy/refined-skill.md"},
    )

    nodes["guard"] = GateNode(
        id="guard",
        evaluator_type="fn",
        evaluator_command=(
            'python3 -c "'
            "from factory.workflow.guard import check; "
            "from pathlib import Path; "
            "s = Path('{project_path}/.factory/strategy/templatized-skill.md').read_text(); "
            "r = Path('{project_path}/.factory/strategy/refined-skill.md').read_text(); "
            "result = check(s, r); "
            "print(result.verdict)"
            '"'
        ),
        reads={
            ".factory/strategy/templatized-skill.md",
            ".factory/strategy/refined-skill.md",
        },
    )

    nodes["split"] = FnNode(
        id="split",
        command="factory workflow export-skills --split {project_path}",
        notes="Split the guard-approved refined skill into clean SKILL.md and SKILL.annotations.yaml.",
        reads={".factory/strategy/refined-skill.md"},
        writes={"skills/SKILL.md", "skills/SKILL.annotations.yaml"},
    )

    edges = [
        Edge(source="dag_sort", target="templatize"),
        Edge(source="templatize", target="review_agent"),
        Edge(source="review_agent", target="guard"),
        Edge(source="guard", target="split", condition=VerdictType.PROCEED),
        Edge(source="guard", target="review_agent", condition=VerdictType.RELOOP),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "skill-refine"

    return Workflow(
        name="skill-refine",
        nodes=nodes,
        edges=edges,
        start_node="dag_sort",
        trigger=trigger,
    )
