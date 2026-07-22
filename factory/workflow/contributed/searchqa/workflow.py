"""SearchQA benchmark workflow — single-node question-answering pipeline.

The agent reads /tmp/task-instruction.md (question + search context),
produces an answer in <answer> tags. No repo to study, no code to merge.
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Workflow,
)

meta = {
    "name": "searchqa",
    "description": (
        "SearchQA benchmark mode — single-node question-answering pipeline. "
        "Reads question + search context from /tmp/task-instruction.md, "
        "produces an answer in <answer> tags."
    ),
}


def workflow() -> Workflow:
    """Build the SearchQA workflow — single builder node."""

    nodes: dict[str, Any] = {
        "builder": AgentNode(
            id="builder",
            role=AgentRole.BUILDER,
            model="sonnet",
            timeout=120,
            prompt_template=(
                "# Question Answering Skill\n\n"
                "(No learned rules yet. Rules will be added through the reflection process.)"
            ),
            writes=set(),
        ),
    }

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "searchqa"

    return Workflow(
        name="searchqa",
        nodes=nodes,
        edges=[],
        start_node="builder",
        terminal=True,
        trigger=trigger,
    )
