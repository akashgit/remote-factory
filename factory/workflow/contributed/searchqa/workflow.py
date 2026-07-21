"""SearchQA benchmark workflow — minimal question-answering pipeline.

3-node pipeline: study → builder → auto_merge
No gate, no retry loop — the simplest possible benchmark workflow.

Designed for Harbor containers where:
- Task instruction is at /tmp/task-instruction.md (question + search context)
- The agent reads the question and search results, produces an answer in <answer> tags
- Harbor checks the MAIN branch for the answer
- No .factory/ infrastructure (no eval, no experiments, no deep-QA)
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    Workflow,
)

meta = {
    "name": "searchqa",
    "description": (
        "SearchQA benchmark mode — minimal 3-node question-answering pipeline. "
        "study → builder → auto_merge. Reads question + search context, "
        "produces an answer in <answer> tags."
    ),
}


def workflow() -> Workflow:
    """Build the SearchQA workflow — study → builder → auto_merge."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 1: Study ──────────────────────────────────────────────
    nodes["study"] = FnNode(
        id="study",
        command=(
            "mkdir -p {project_path}/.factory/reviews && "
            "cd {project_path} && "
            "("
            "echo '=== Task Instruction ===' && "
            "cat /tmp/task-instruction.md 2>/dev/null || "
            "echo 'No task instruction file found at /tmp/task-instruction.md'"
            ") > .factory/reviews/study-output.md 2>&1"
        ),
        writes={".factory/reviews/study-output.md"},
    )

    # ── Node 2: Builder (QA agent) ─────────────────────────────────
    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        model="sonnet",
        timeout=300,
        prompt_template=(
            "# Question Answering Skill\n\n"
            "(No learned rules yet. Rules will be added through the reflection process.)\n\n"
            "## Task\n\n"
            "Read the question and search results from /tmp/task-instruction.md.\n"
            "Answer the question using ONLY the information in the search results.\n"
            "Put your final answer inside <answer> tags.\n\n"
            "Example: <answer>Paris</answer>\n"
        ),
        reads={".factory/reviews/study-output.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    # ── Node 3: Auto Merge ─────────────────────────────────────────
    nodes["auto_merge"] = FnNode(
        id="auto_merge",
        command=(
            "cd {project_path} && "
            "CURRENT=$(git rev-parse --abbrev-ref HEAD) && "
            "COMMON=$(git rev-parse --git-common-dir) && "
            "BASE=$(git --git-dir=\"$COMMON\" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main) && "
            "if [ \"$CURRENT\" = \"$BASE\" ]; then "
            "echo \"Already on $BASE — no merge needed\"; "
            "exit 0; fi && "
            "git update-ref refs/heads/\"$BASE\" HEAD && "
            "PARENT_WT=$(cd \"$COMMON/..\" && pwd) && "
            "git diff-tree --no-commit-id --name-only -r HEAD HEAD~1 | "
            "while read file; do "
            "if [ -f \"$file\" ]; then "
            "mkdir -p \"$PARENT_WT/$(dirname $file)\" && "
            "cp \"$file\" \"$PARENT_WT/$file\"; "
            "fi; done && "
            "echo \"Updated $BASE to $(git rev-parse --short HEAD)\""
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # ── Edges ──────────────────────────────────────────────────────
    edges = [
        Edge(source="study", target="builder"),
        Edge(source="builder", target="auto_merge"),
    ]

    # ── Trigger ────────────────────────────────────────────────────
    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "searchqa"

    return Workflow(
        name="searchqa",
        nodes=nodes,
        edges=edges,
        start_node="study",
        terminal=True,
        trigger=trigger,
    )
