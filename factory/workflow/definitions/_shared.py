"""Shared helpers used by multiple workflow definitions."""

from __future__ import annotations

from typing import Any

from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    GateNode,
    VerdictType,
)

DOC_FRESHNESS_GATE_PROMPT = (
    "Check the PR diff for documentation freshness. "
    "If public APIs, CLI commands, configuration options, "
    "or architecture were changed or added, corresponding documentation "
    "(README.md, CLAUDE.md, docstrings, --help text, or doc/ files) "
    "MUST be updated. PROCEED if docs are current or no doc-worthy changes "
    "exist. RELOOP to builder if documentation is stale — specify exactly "
    "which changes need doc updates."
)


def _deep_qa_subgraph(
    *,
    code_reviewer_extra: str = "",
    adversarial_extra: str = "",
) -> tuple[dict[str, Any], list[Edge]]:
    """Return (nodes, internal_edges) for the 4-node deep-qa verification subgraph.

    Three specialist agents run sequentially with a single gate after
    code_reviewer to short-circuit on critical bugs:

        health_checker → code_reviewer → gate_review → adversarial_tester

    Agent prompts live in their role .md files; prompt_template is only set
    when a workflow passes extra context via code_reviewer_extra / adversarial_extra.
    The caller wires the entry edge (→ health_checker) and the exit edge
    (adversarial_tester →) into the surrounding workflow.
    """
    nodes: dict[str, Any] = {}

    nodes["health_checker"] = AgentNode(
        id="health_checker",
        role=AgentRole.HEALTH_CHECKER,
        reads={".factory/reviews/builder-latest.md", ".factory/strategy/current.md"},
        writes={".factory/reviews/health-check.md"},
    )

    nodes["code_reviewer"] = AgentNode(
        id="code_reviewer",
        role=AgentRole.CODE_REVIEWER,
        prompt_template=code_reviewer_extra,
        reads={".factory/reviews/builder-latest.md", ".factory/strategy/current.md"},
        writes={".factory/reviews/code-review.md"},
    )

    nodes["gate_review"] = GateNode(
        id="gate_review",
        evaluator_type="fn",
        evaluator_command=(
            "if grep -q 'CRITICAL_FOUND' "
            "{project_path}/.factory/reviews/code-review.md; "
            "then echo 'FAIL: critical issues found'; "
            "else echo 'PROCEED'; fi"
        ),
        reads={".factory/reviews/code-review.md"},
    )

    nodes["adversarial_tester"] = AgentNode(
        id="adversarial_tester",
        role=AgentRole.ADVERSARIAL_TESTER,
        timeout=1800,
        prompt_template=adversarial_extra,
        reads={".factory/reviews/builder-latest.md", ".factory/strategy/current.md"},
        writes={".factory/reviews/adversarial-qa.md"},
    )

    internal_edges = [
        Edge(source="health_checker", target="code_reviewer"),
        Edge(source="code_reviewer", target="gate_review"),
        Edge(source="gate_review", target="adversarial_tester", condition=VerdictType.PROCEED),
    ]

    return nodes, internal_edges
