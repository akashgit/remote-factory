"""SearchQA benchmark workflow — single-node question-answering pipeline.

The agent reads /tmp/task-instruction.md (question + search context),
produces an answer in <answer> tags. No repo to study, no code to merge.
"""

import base64
import os
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

_DEFAULT_SKILL = (
    "# Question Answering Skill\n\n"
    "(No learned rules yet. Rules will be added through the reflection process.)"
)

_FIXED_INSTRUCTIONS = (
    "\n\n## Instructions\n\n"
    "Read the question and search results from /tmp/task-instruction.md.\n"
    "Answer the question and write ONLY your final answer to /workspace/answer.txt.\n"
    "Also include your answer in <answer> tags in your response.\n"
)


def _resolve_prompt() -> str:
    b64 = os.environ.get("SEARCHQA_SKILL_B64")
    skill = base64.b64decode(b64).decode() if b64 else _DEFAULT_SKILL
    return skill + _FIXED_INSTRUCTIONS


def workflow() -> Workflow:
    """Build the SearchQA workflow — single builder node."""

    nodes: dict[str, Any] = {
        "builder": AgentNode(
            id="builder",
            role=AgentRole.BUILDER,
            model="sonnet",
            timeout=120,
            prompt_template=_resolve_prompt(),
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
