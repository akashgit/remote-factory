"""Deep-QA standalone verification workflow.

Runs the decomposed QA pipeline (health_checker → code_reviewer →
adversarial_tester) with a gate after code review as a standalone mode.
Triggered via `factory workflow run deep-qa` or `factory ceo /path --mode deep-qa`.
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.definitions import _deep_qa_subgraph
from factory.workflow.primitives import AgentNode, Edge, FnNode, GateNode, VerdictType, Workflow

meta = {
    "name": "deep-qa",
    "description": (
        "Standalone deep-QA verification pipeline — 3 sequential specialist "
        "agents (health_checker, code_reviewer, adversarial_tester) with a gate "
        "after code review to short-circuit on critical bugs."
    ),
}


def workflow() -> Workflow:
    """Build the standalone deep-qa workflow."""
    dq_nodes, dq_edges = _deep_qa_subgraph()

    for nid in ("health_checker", "code_reviewer", "adversarial_tester"):
        node = dq_nodes[nid]
        assert isinstance(node, AgentNode)
        dq_nodes[nid] = node.model_copy(update={"reads": set()})

    nodes: dict[str, Any] = {**dq_nodes}

    nodes["gate_precheck"] = GateNode(
        id="gate_precheck",
        evaluator_type="fn",
        evaluator_command="factory precheck {project_path} --score-before 0 --score-after 0",
        reads={".factory/reviews/adversarial-qa.md"},
    )

    nodes["post_review"] = FnNode(
        id="post_review",
        command=(
            "factory review --verdict $VERDICT --pr $PR_NUMBER"
            " --score-before $SCORE_BEFORE --score-after $SCORE_AFTER"
        ),
        reads={".factory/reviews/adversarial-qa.md"},
    )

    edges = [
        *dq_edges,
        Edge(source="adversarial_tester", target="gate_precheck"),
        Edge(source="gate_precheck", target="post_review", condition=VerdictType.PROCEED),
        Edge(source="gate_precheck", target="post_review", condition=VerdictType.HALT),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "deep-qa"

    return Workflow(
        name="deep-qa",
        nodes=nodes,
        edges=edges,
        start_node="health_checker",
        trigger=trigger,
    )
