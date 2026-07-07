"""TerminalBench benchmark workflow — minimal pipeline for terminal debugging tasks.

4-node pipeline: study → builder → gate_verify → auto_merge
RELOOP from gate_verify back to builder (max 3 iterations) on failure.

Designed for Harbor containers where:
- Task instruction is at /tmp/task-instruction.md
- Tasks are terminal/command-line debugging problems (e.g., recovering from broken git state)
- Harbor's verifier is the FINAL authority on pass/fail
- Harbor checks the MAIN branch for changes
- No .factory/ infrastructure (no eval, no experiments, no deep-QA)
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)

meta = {
    "name": "terminalbench",
    "description": (
        "TerminalBench benchmark mode — minimal 4-node pipeline for solving "
        "terminal/command-line debugging problems in containerized evaluation. "
        "study → builder → gate_verify → auto_merge with RELOOP on failure."
    ),
}


def workflow() -> Workflow:
    """Build the TerminalBench workflow from scratch (not composed from improve)."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 1: Study ──────────────────────────────────────────────
    nodes["study"] = FnNode(
        id="study",
        command=(
            "mkdir -p {project_path}/.factory/reviews && "
            "cd {project_path} && "
            "("
            "echo '=== Terminal Environment ===' && "
            "echo '--- Shell & OS ---' && "
            "echo \"SHELL=$SHELL\" && "
            "uname -a 2>/dev/null || true && "
            "echo '\\n--- Git Status ---' && "
            "git status 2>/dev/null || echo 'Not a git repository' && "
            "echo '\\n--- Git Log ---' && "
            "git log --oneline -10 2>/dev/null || true && "
            "echo '\\n--- File Listing ---' && "
            "ls -la && "
            "echo '\\n=== Shell Scripts ===' && "
            "find . -type f \\( -name '*.sh' -o -name '*.bash' -o -name '*.zsh' \\) 2>/dev/null | head -50 && "
            "echo '\\n=== Configuration Files ===' && "
            "ls -la .gitconfig .bashrc .zshrc .profile Makefile Dockerfile 2>/dev/null || true && "
            "echo '\\n=== Task Instruction ===' && "
            "cat /tmp/task-instruction.md 2>/dev/null || "
            "echo 'No task instruction file found at /tmp/task-instruction.md'"
            ") > .factory/reviews/study-output.md 2>&1"
        ),
        writes={".factory/reviews/study-output.md"},
    )

    # ── Node 2: Builder ────────────────────────────────────────────
    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        model="opus",
        timeout=1200,
        max_iterations=3,
        prompt_template=(
            "You are solving a terminal/command-line debugging problem for the "
            "TerminalBench benchmark.\n\n"
            "## Your Task\n\n"
            "1. **Read the task instruction** — Read /tmp/task-instruction.md for the full "
            "problem description and expected outcome.\n\n"
            "2. **Explore the terminal environment** — check git status, shell state, "
            "file system layout, environment variables, running processes, and any other "
            "relevant system state. Understand what is broken or misconfigured.\n\n"
            "3. **Diagnose the issue** — identify the root cause of the terminal/CLI "
            "problem described in the task.\n\n"
            "4. **Implement the fix** — execute the fix directly by running commands, "
            "editing files, or reconfiguring the environment. Do whatever is needed to "
            "resolve the issue.\n\n"
            "5. **Verify the fix** — test that the expected behavior now works. "
            "Run the relevant commands to confirm the issue is resolved.\n\n"
            "6. **Commit your changes** — commit directly on the current branch "
            "with a descriptive message. Do NOT create a new branch. Do NOT create a PR.\n\n"
            "## Rules\n\n"
            "- Act AUTONOMOUSLY — do NOT ask for confirmation or input\n"
            "- Execute commands directly — this is a terminal debugging task\n"
            "- MUST verify the fix works before committing\n"
            "- Do NOT create branches or PRs — commit on current branch\n"
            "- Do NOT run factory commands (factory eval, factory study, etc.)\n"
            "- If something fails, investigate and try alternative approaches\n"
        ),
        reads={".factory/reviews/study-output.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    # ── Node 3: Gate Verify ────────────────────────────────────────
    nodes["gate_verify"] = GateNode(
        id="gate_verify",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            "CHANGES=$(git diff HEAD~1 --stat 2>/dev/null || echo 'NO_COMMITS') && "
            "if [ \"$CHANGES\" = 'NO_COMMITS' ] || [ -z \"$CHANGES\" ]; then "
            "echo 'fail: builder did not commit any changes'; "
            "exit 0; fi && "
            "BUILDER_OUTPUT=$(cat .factory/reviews/builder-latest.md 2>/dev/null || echo '') && "
            "if echo \"$BUILDER_OUTPUT\" | grep -qiE 'tests?.*(pass|succeed|ok|PASSED)'; then "
            "echo 'pass: builder reports tests passing'; "
            "elif echo \"$BUILDER_OUTPUT\" | grep -qiE '(fix|resolv|verif).*(work|success|confirm|done)'; then "
            "echo 'pass: builder reports fix verified'; "
            "elif echo \"$BUILDER_OUTPUT\" | grep -qiE 'tests?.*(fail|error|FAILED)'; then "
            "echo 'reloop: builder needs to retry — tests did not pass'; "
            "elif echo \"$BUILDER_OUTPUT\" | grep -qiE '(fail|error|broken|cannot)'; then "
            "echo 'reloop: builder needs to retry — fix not confirmed'; "
            "else "
            "echo 'pass: changes committed, no issues detected'; "
            "fi"
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # ── Node 4: Auto Merge ─────────────────────────────────────────
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
        Edge(source="builder", target="gate_verify"),
        Edge(source="gate_verify", target="auto_merge", condition=VerdictType.PROCEED),
        Edge(source="gate_verify", target="builder", condition=VerdictType.RELOOP),
    ]

    # ── Trigger ────────────────────────────────────────────────────

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "terminalbench"

    return Workflow(
        name="terminalbench",
        nodes=nodes,
        edges=edges,
        start_node="study",
        terminal=True,
        trigger=trigger,
    )
