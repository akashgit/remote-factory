"""SearchQA benchmark workflow — single-node question-answering pipeline.

The agent reads /tmp/task-instruction.md (question + search context),
produces an answer in <answer> tags. No repo to study, no code to merge.

Prompt override: set FACTORY_WORKFLOW_YAML_B64 env var with base64-encoded
YAML annotations to override slot values at runtime.
Use ``factory workflow run searchqa . --from-yaml <path>`` for local testing.
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

_DEFAULT_PROMPT = (
    "# Question Answering Skill\n\n"
    "(No learned rules yet. Rules will be added through the reflection process.)"
    "\n\n## Instructions\n\n"
    "Read the question and search results from /tmp/task-instruction.md.\n"
    "Answer the question and write ONLY your final answer to /workspace/answer.txt.\n"
    "Also include your answer in <answer> tags in your response.\n"
)


def _resolve_prompt() -> str:
    """Return the prompt template, preferring SEARCHQA_SKILL_B64 if set."""
    raw = os.environ.get("SEARCHQA_SKILL_B64", "")
    if raw:
        try:
            return base64.b64decode(raw).decode()
        except Exception:
            pass
    return _DEFAULT_PROMPT


def workflow() -> Workflow:
    """Build the SearchQA workflow — single builder node."""
    prompt = _resolve_prompt()

    nodes: dict[str, Any] = {
        "builder": AgentNode(
            id="builder",
            role=AgentRole.BUILDER,
            model="sonnet",
            timeout=120,
            prompt_template=prompt,
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
