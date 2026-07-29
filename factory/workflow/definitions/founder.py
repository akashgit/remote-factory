"""W9: Founder Mode workflow definition."""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    Study,
    VerdictType,
    Workflow,
)


def founder_workflow() -> Workflow:
    """W9: Founder Mode — rapid prototyping pipeline for fast hypothesis iteration.

    Study -> Strategist -> Builder -> gate_tests -> finalize(async)

    No research, no deep-QA, no eval scoring. Terminal — does not chain to
    other modes. Uses pass/fail tests only.
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # Study
    nodes["study"] = Study(
        id="study",
        command="factory study {project_path}",
        writes={".factory/strategy/observations.md"},
    )

    # Strategist — pick ONE hypothesis, skip FEEC/backlog
    nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Pick ONE high-leverage hypothesis to prototype. "
            "Read observations at .factory/strategy/observations.md. "
            "Skip FEEC classification and backlog grooming — just pick the most "
            "promising idea and write it to .factory/strategy/current.md. "
            "Keep it scoped: one idea, one PR, fast to implement."
        ),
        reads={".factory/strategy/observations.md"},
        writes={".factory/strategy/current.md"},
    )

    # Builder — prototype quickly
    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        prompt_template=(
            "Prototype the hypothesis from .factory/strategy/current.md. "
            "Read CLAUDE.md and factory.md for project context. "
            "Prioritize getting something working over code quality. "
            "Skip edge cases and comprehensive error handling. "
            "Run tests to verify it works. Commit the changes."
        ),
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    # Gate — pytest + ruff pass/fail
    nodes["gate_tests"] = GateNode(
        id="gate_tests",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && python -m pytest --tb=short -q 2>&1 && "
            "ruff check . 2>&1"
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # Finalize — record results, bypassing precheck (no eval scores in founder mode)
    nodes["finalize"] = FnNode(
        id="finalize",
        command=(
            "factory finalize {project_path}"
            " --id $EXP_ID"
            " --verdict $VERDICT"
            ' --hypothesis "$HYPOTHESIS"'
            " --force"
        ),
        notes=(
            "Record experiment to .factory/results.tsv, bypassing precheck gates "
            "(no QA agents or eval scores in founder mode). "
            "The CEO must substitute $EXP_ID, $VERDICT (keep/revert), and $HYPOTHESIS."
        ),
        reads={".factory/reviews/builder-latest.md"},
        writes={".factory/experiments/verdict.json"},
        blocking=False,
    )

    edges = [
        Edge(source="study", target="strategist"),
        Edge(source="strategist", target="builder"),
        Edge(source="builder", target="gate_tests"),
        Edge(source="gate_tests", target="finalize", condition=VerdictType.PROCEED),
        Edge(source="gate_tests", target="builder", condition=VerdictType.RELOOP),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state == ProjectState.HAS_FACTORY and ctx.get("mode") == "founder"

    return Workflow(
        name="founder",
        nodes=nodes,
        edges=edges,
        start_node="study",
        trigger=trigger,
        terminal=True,
    )
