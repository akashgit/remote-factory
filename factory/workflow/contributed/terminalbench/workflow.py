"""TerminalBench benchmark workflow — pipeline for real-world engineering tasks.

4-node pipeline: study → builder → gate_verify → auto_merge
RELOOP from gate_verify back to builder (max 3 iterations) on failure.

Designed for Harbor containers where:
- Task instruction is at /tmp/task-instruction.md
- Tasks span software engineering, scientific computing, system administration,
  security, ML, data processing, debugging, file operations, and more
- The common thread: agent operates in a terminal and must independently
  navigate complex real-world tasks
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
        "TerminalBench benchmark mode — 4-node pipeline for solving "
        "real-world engineering tasks in terminal environments, from compiling "
        "legacy software to scientific computing to system configuration. "
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
            "echo '=== Workspace ===' && "
            "ls -la && "
            "echo '\\n=== Git ===' && "
            "git status 2>/dev/null || echo 'Not a git repository' && "
            "git log --oneline -10 2>/dev/null || true && "
            "echo '\\n=== Languages ===' && "
            "(python3 --version 2>/dev/null || true) && "
            "(gcc --version 2>/dev/null | head -1 || true) && "
            "(g++ --version 2>/dev/null | head -1 || true) && "
            "(rustc --version 2>/dev/null || true) && "
            "(go version 2>/dev/null || true) && "
            "(node --version 2>/dev/null || true) && "
            "(java -version 2>&1 | head -1 || true) && "
            "(R --version 2>/dev/null | head -1 || true) && "
            "echo '\\n=== Package Managers ===' && "
            "(which pip pip3 apt npm cargo gem luarocks 2>/dev/null || true) && "
            "echo '\\n=== Tools ===' && "
            "(which make cmake git curl wget docker "
            "gdb strace ltrace valgrind sqlite3 ffmpeg "
            "openssl nmap 2>/dev/null || true) && "
            "echo '\\n=== Task ===' && "
            "cat /tmp/task-instruction.md 2>/dev/null || "
            "echo 'No task instruction found at /tmp/task-instruction.md'"
            ") > .factory/reviews/study-output.md 2>&1"
        ),
        writes={".factory/reviews/study-output.md"},
    )

    # ── Node 2: Builder ────────────────────────────────────────────
    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        model="opus",
        timeout=7200,
        max_iterations=3,
        prompt_template=(
            "You are solving a real-world engineering task in a terminal environment.\n\n"
            "## Your Task\n\n"
            "1. **Read the task instruction** — Read /tmp/task-instruction.md carefully. "
            "Understand exactly what the task is asking you to produce or accomplish, "
            "including any expected output format or success criteria.\n\n"
            "2. **Understand the task type** — Tasks can range widely: building or "
            "debugging software, scientific computing, system administration, security "
            "analysis, data processing, ML model work, file format manipulation, "
            "mathematical computation, and more. Identify what kind of problem this is "
            "before diving in.\n\n"
            "3. **Explore the environment** — Check what languages, compilers, tools, and "
            "package managers are available. Review the study output for an environment "
            "summary. Examine the workspace files and directory structure to understand "
            "what you are working with.\n\n"
            "4. **Install dependencies** — If the task requires tools, libraries, or "
            "packages that are not already installed, install them using the available "
            "package manager (apt, pip, npm, cargo, etc.). Do this proactively before "
            "attempting the solution.\n\n"
            "5. **Implement the solution** — Write code, compile programs, configure "
            "services, run analyses, execute commands — whatever the task requires. "
            "Work methodically: break complex tasks into steps and verify each step "
            "before moving on.\n\n"
            "6. **Verify the result** — Test that your solution produces the expected "
            "output or achieves the expected outcome. Re-read the task instruction to "
            "confirm you have not missed any requirements.\n\n"
            "7. **Commit your changes** — Commit directly on the current branch "
            "with a descriptive message. Do NOT create a new branch. Do NOT create a PR.\n\n"
            "## Rules\n\n"
            "- Act AUTONOMOUSLY — do NOT ask for confirmation or input\n"
            "- Read the FULL task instruction before starting — details matter\n"
            "- Install any missing dependencies proactively — do not assume they exist\n"
            "- MUST verify the result matches expected output before committing\n"
            "- Do NOT create branches or PRs — commit on current branch\n"
            "- Do NOT run factory commands (factory eval, factory study, etc.)\n"
            "- If something fails, investigate root cause and try alternative approaches\n"
            "- If a tool or library is unavailable, find or build an alternative\n"
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
            "if echo \"$BUILDER_OUTPUT\" | grep -qiE '(pass|succeed|ok|complete|done|verified|correct|works)'; then "
            "echo 'pass: builder reports task completed successfully'; "
            "elif echo \"$BUILDER_OUTPUT\" | grep -qiE '(fail|error|broken|cannot|unable|wrong)'; then "
            "echo 'reloop: builder needs to retry — solution not confirmed'; "
            "else "
            "echo 'pass: changes committed, no failure signals detected'; "
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
