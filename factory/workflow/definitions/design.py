"""W2: Design Mode workflow definition."""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.definitions.build import build_workflow
from factory.workflow.primitives import GateNode, Workflow


def design_workflow() -> Workflow:
    """W2: Design Mode — W1 with user gate at strategy approval.

    W2 = W1[gate_strategy <- GateNode(user)]
    """
    wf = build_workflow()

    wf.nodes["gate_strategy"] = GateNode(
        id="gate_strategy",
        evaluator_type="user",
        reads={".factory/strategy/current.md"},
    )

    wf.name = "design"

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state in {ProjectState.NO_REPO, ProjectState.REPO_INCOMPLETE} and ctx.get(
            "interactive", False
        )

    wf.trigger = trigger
    return wf
