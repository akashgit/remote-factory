"""W8: Refine Mode workflow definition."""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.definitions._shared import DOC_FRESHNESS_GATE_PROMPT, _deep_qa_subgraph
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)


def refine_workflow() -> Workflow:
    """W8: Refine Mode — lightweight user-directed refinement pipeline.

    Refiner -> CEO gate -> tier gate -> begin -> create issue ->
    Builder -> deep-QA -> gate_qa(max 3) -> precheck -> finalize -> Archivist(async)
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # R0: Classify
    nodes["refiner"] = AgentNode(
        id="refiner",
        role=AgentRole.REFINER,
        prompt_template=(
            "Classify and scope a refinement request. "
            "Read CLAUDE.md and factory.md. Analyze the codebase to identify "
            "which files need to change, estimate scope, and classify the request "
            "as Tier 1, 2, or 3. Produce the structured classification output "
            "with a Builder task description. "
            "Write the refinement plan to .factory/strategy/current.md."
        ),
        writes={".factory/reviews/refiner-latest.md", ".factory/strategy/current.md"},
    )

    # R0-review: CEO Review
    nodes["gate_refiner"] = GateNode(
        id="gate_refiner",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review Refiner classification. Is the tier classification reasonable? "
            "Are the identified files correct? Is the Builder task description "
            "specific enough? REDIRECT if the classification is wrong."
        ),
        reads={".factory/reviews/refiner-latest.md"},
    )

    # R1: Tier gate — Tier 3 exits
    nodes["gate_tier"] = GateNode(
        id="gate_tier",
        evaluator_type="fn",
        evaluator_command=(
            'python3 -c "'
            "from pathlib import Path; "
            "text = Path('{project_path}/.factory/reviews/refiner-latest.md').read_text(); "
            "print('HALT' if 'Tier 3' in text or 'tier 3' in text or 'TIER 3' in text else 'PROCEED')"
            '"'
        ),
        reads={".factory/reviews/refiner-latest.md"},
    )

    # R2: Begin experiment
    nodes["begin"] = FnNode(
        id="begin",
        command='factory begin {project_path} --hypothesis "$HYPOTHESIS"',
        notes="Open a new experiment for the refinement. The CEO must substitute $HYPOTHESIS with the refinement description.",
        writes={".factory/experiments/current_id"},
    )

    # R3: Create GitHub issue
    nodes["create_issue"] = FnNode(
        id="create_issue",
        command=(
            'gh issue create --title "Refine: refinement request" '
            '--label "refinement" --body "Factory refinement experiment."'
        ),
        notes="Create a GitHub issue to track the refinement. Must run after begin so the experiment ID is available.",
        reads={".factory/reviews/refiner-latest.md"},
    )

    # R4: Builder
    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        prompt_template=(
            "Implement the refinement described in the Refiner's output. "
            "Read the GitHub issue. Read CLAUDE.md and factory.md. "
            "Implement exactly what the issue describes. Run tests. "
            "Commit and open a draft PR."
        ),
        reads={".factory/reviews/refiner-latest.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    # R5: Deep-QA verification (replaces monolithic QA)
    dq_nodes, dq_edges = _deep_qa_subgraph(
        code_reviewer_extra=(
            "Run `factory guard --check-scope` to verify the refinement "
            "stays within declared scope."
        ),
    )
    nodes.update(dq_nodes)

    # R5-review: CEO gate on QA
    nodes["gate_qa"] = GateNode(
        id="gate_qa",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Read QA output. Did all verification sections pass? "
            "Are there issues that need Builder fixes? "
            "REDIRECT to Builder if issues found (max 3 iterations)."
        ),
        reads={
            ".factory/reviews/health-check.md",
            ".factory/reviews/code-review.md",
            ".factory/reviews/adversarial-qa.md",
        },
    )

    nodes["gate_doc_freshness"] = GateNode(
        id="gate_doc_freshness",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=DOC_FRESHNESS_GATE_PROMPT,
        reads={".factory/reviews/adversarial-qa.md"},
    )

    # R6: Precheck gate
    nodes["gate_precheck"] = GateNode(
        id="gate_precheck",
        evaluator_type="fn",
        evaluator_command="factory precheck {project_path} --score-before 0 --score-after 0",
        reads={".factory/reviews/adversarial-qa.md"},
    )

    # R7: Finalize
    nodes["finalize"] = FnNode(
        id="finalize",
        command=(
            "factory finalize {project_path}"
            " --id $EXP_ID"
            " --verdict $VERDICT"
            ' --hypothesis "$HYPOTHESIS"'
        ),
        notes="Close the refinement experiment with a verdict. The CEO must substitute $EXP_ID, $VERDICT (keep/revert/error), and $HYPOTHESIS.",
        reads={".factory/reviews/adversarial-qa.md"},
        writes={".factory/experiments/verdict.json"},
    )

    # R12: Archivist (async)
    nodes["archivist"] = AgentNode(
        id="archivist",
        role=AgentRole.ARCHIVIST,
        prompt_template="Archive refinement experiment results and learnings.",
        reads={".factory/experiments/verdict.json"},
        writes={".factory/archive/refinement.md"},
        blocking=False,
    )

    edges = [
        # Refiner -> CEO gate
        Edge(source="refiner", target="gate_refiner"),
        Edge(source="gate_refiner", target="gate_tier", condition=VerdictType.PROCEED),
        Edge(source="gate_refiner", target="refiner", condition=VerdictType.RELOOP),
        # Tier gate -> begin (proceed) or halt (tier 3)
        Edge(source="gate_tier", target="begin", condition=VerdictType.PROCEED),
        # Begin -> create issue -> builder
        Edge(source="begin", target="create_issue"),
        Edge(source="create_issue", target="builder"),
        # Builder -> deep-qa directly (no gate_build in refine)
        Edge(source="builder", target="health_checker"),
        # Deep-QA internal edges
        *dq_edges,
        # adversarial_tester -> gate_qa
        Edge(source="adversarial_tester", target="gate_qa"),
        Edge(source="gate_qa", target="gate_doc_freshness", condition=VerdictType.PROCEED),
        Edge(source="gate_qa", target="builder", condition=VerdictType.RELOOP),
        # Doc freshness -> precheck (proceed) or builder (reloop)
        Edge(source="gate_doc_freshness", target="gate_precheck", condition=VerdictType.PROCEED),
        Edge(source="gate_doc_freshness", target="builder", condition=VerdictType.RELOOP),
        # Precheck -> finalize (proceed) or halt -> archivist (error handling)
        Edge(source="gate_precheck", target="finalize", condition=VerdictType.PROCEED),
        Edge(source="gate_precheck", target="archivist", condition=VerdictType.HALT),
        Edge(source="finalize", target="archivist"),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state == ProjectState.HAS_FACTORY and bool(ctx.get("refine"))

    return Workflow(
        name="refine",
        nodes=nodes,
        edges=edges,
        start_node="refiner",
        trigger=trigger,
    )
