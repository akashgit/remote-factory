"""SWE-bench benchmark workflow — minimal bug-fix pipeline for containerized evaluation.

4-node pipeline: study → builder → gate_verify → auto_merge
RELOOP from gate_verify back to builder (max 3 iterations) on test failure.

Designed for Harbor containers where:
- Task instruction is at /tmp/task-instruction.md (passed via --prompt)
- Harbor's pytest verifier is the FINAL authority on pass/fail
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
    "name": "swebench",
    "description": (
        "SWE-bench benchmark mode — minimal 4-node pipeline for solving "
        "GitHub issues in containerized evaluation. study → builder → "
        "gate_verify → auto_merge with RELOOP on test failure."
    ),
}


def workflow() -> Workflow:
    """Build the SWE-bench workflow from scratch (not composed from improve)."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 1: Study ──────────────────────────────────────────────
    nodes["study"] = FnNode(
        id="study",
        command=(
            "mkdir -p {project_path}/.factory/reviews && "
            "cd {project_path} && "
            "("
            "echo '=== Repository Structure ===' && "
            "find . -type f -name '*.py' | head -200 && "
            "echo '\\n=== Test Files ===' && "
            "find . -type f -name 'test_*.py' -o -name '*_test.py' | head -50 && "
            "echo '\\n=== Configuration Files ===' && "
            "ls -la setup.py setup.cfg pyproject.toml tox.ini conftest.py 2>/dev/null || true && "
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
            "You are fixing a bug in an open-source project for the SWE-bench benchmark.\n\n"
            "## Your Task\n\n"
            "1. **Read the task instruction** — Read /tmp/task-instruction.md for the full "
            "bug description and task requirements.\n\n"
            "2. **Understand the codebase** — explore the repository structure. "
            "Read relevant source files, test files, and configuration. "
            "Identify the root cause of the bug described in the task.\n\n"
            "3. **Implement the fix** — make the MINIMAL change that resolves the "
            "issue. Do NOT refactor, modernize, or add unrelated improvements. "
            "Fix ONLY the described bug.\n\n"
            "4. **Run the project's own tests** — this is CRITICAL. Run the test "
            "suite to verify your fix works AND existing tests still pass. "
            "Use pytest, tox, or whatever test runner the project uses. "
            "If specific test files are mentioned in the task, run those first.\n\n"
            "5. **Commit your changes** — commit directly on the current branch "
            "with a descriptive message referencing the issue. Do NOT create a "
            "new branch. Do NOT create a PR.\n\n"
            "## Rules\n\n"
            "- MINIMAL fix only — smallest diff that resolves the issue\n"
            "- MUST run tests before committing — never commit untested code\n"
            "- Do NOT create branches or PRs — commit on current branch\n"
            "- Do NOT run factory commands (factory eval, factory study, etc.)\n"
            "- Do NOT modify test files unless the bug is IN the test infrastructure\n"
            "- If tests fail after your fix, investigate and fix the issue\n"
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
            "echo 'HALT: No commits found — builder did not commit changes'; "
            "exit 0; fi && "
            "echo \"Changes committed: $CHANGES\" && "
            "BUILDER_OUTPUT=$(cat .factory/reviews/builder-latest.md 2>/dev/null || echo '') && "
            "if echo \"$BUILDER_OUTPUT\" | grep -qiE 'tests?.*(pass|succeed|ok|PASSED)'; then "
            "echo 'PROCEED: Builder reports tests passing'; "
            "elif echo \"$BUILDER_OUTPUT\" | grep -qiE 'tests?.*(fail|error|FAILED)'; then "
            "echo 'RELOOP: Builder reports test failures — retry'; "
            "else "
            "echo 'PROCEED: Changes committed, no explicit test failure reported'; "
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
            "if [ \"$CURRENT\" = 'main' ] || [ \"$CURRENT\" = 'master' ]; then "
            "echo 'Already on main/master branch — no merge needed'; "
            "exit 0; fi && "
            "BASE=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null "
            "| sed 's|refs/remotes/origin/||' || echo main) && "
            "git checkout \"$BASE\" && "
            "git merge --no-edit \"$CURRENT\""
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
        return ctx.get("mode") == "swebench"

    return Workflow(
        name="swebench",
        nodes=nodes,
        edges=edges,
        start_node="study",
        trigger=trigger,
    )
