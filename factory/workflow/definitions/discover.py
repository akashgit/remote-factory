"""W6: Discover Mode workflow definition."""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)


def discover_workflow() -> Workflow:
    """W6: Discover Mode — auto-discover eval dimensions and generate eval harness.

    factory discover -> CEO verify -> re-detect state
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    nodes["discover"] = FnNode(
        id="discover",
        command="factory discover {project_path}",
        notes="Auto-discover eval dimensions and generate the eval harness (eval_profile.json + eval/score.py).",
        writes={
            ".factory/eval_profile.json",
            "eval/score.py",
        },
    )

    nodes["gate_discover"] = GateNode(
        id="gate_discover",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Verify the discovered eval profile makes sense. "
            "Read .factory/eval_profile.json and eval/score.py. "
            "Check: Are the dimensions relevant to this project? "
            "Does score.py look correct? Any missing dimensions?"
        ),
        reads={".factory/eval_profile.json", "eval/score.py"},
    )

    nodes["redetect"] = FnNode(
        id="redetect",
        command="factory detect {project_path}",
        notes="Re-detect project state after discovery to transition out of no_factory state.",
        reads={".factory/eval_profile.json"},
    )

    edges = [
        Edge(source="discover", target="gate_discover"),
        Edge(source="gate_discover", target="redetect", condition=VerdictType.PROCEED),
        Edge(source="gate_discover", target="discover", condition=VerdictType.RELOOP),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state == ProjectState.NO_FACTORY

    return Workflow(
        name="discover",
        nodes=nodes,
        edges=edges,
        start_node="discover",
        trigger=trigger,
    )
