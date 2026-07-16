"""Legacy-Bench benchmark workflow — lean pipeline for legacy code bugs.

4-node pipeline: study → builder → gate_verify → auto_merge
RELOOP from gate_verify back to builder (max 3 iterations) on failure.

Designed for Harbor containers where:
- Task instruction is at /tmp/task-instruction.md
- Targets legacy code: COBOL, Fortran, C, Java 7, Assembly
- The benchmark uses hidden test inputs — solutions must be general algorithms
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
    "name": "legacybench",
    "description": (
        "Legacy-Bench benchmark mode — 4-node pipeline for fixing bugs in "
        "legacy code (COBOL, Fortran, C, Java 7, Assembly). "
        "study → builder → gate_verify → auto_merge with RELOOP on failure."
    ),
}


def workflow() -> Workflow:
    """Build the Legacy-Bench workflow as a lean 4-node pipeline."""
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
            "echo '\\n=== Source Files ===' && "
            "find . -type f \\( "
            "-name '*.c' -o -name '*.h' -o -name '*.f' -o -name '*.f90' "
            "-o -name '*.cob' -o -name '*.cbl' -o -name '*.java' "
            "-o -name '*.s' -o -name '*.asm' -o -name '*.py' "
            "\\) | head -100 && "
            "echo '\\n=== Git ===' && "
            "git status 2>/dev/null || echo 'Not a git repository' && "
            "git log --oneline -10 2>/dev/null || true && "
            "echo '\\n=== Build System ===' && "
            "cat Makefile 2>/dev/null || true && "
            "ls -la *.sh build* configure* 2>/dev/null || true && "
            "echo '\\n=== Test Files ===' && "
            "find . -type f \\( "
            "-name 'test*' -o -name '*test*' -o -name '*spec*' "
            "\\) 2>/dev/null | head -50 || true && "
            "echo '\\n=== Task ===' && "
            "cat /tmp/task-instruction.md 2>/dev/null || "
            "echo 'No task instruction found at /tmp/task-instruction.md' && "
            "echo '\\n=== Output Format Analysis ===' && "
            "echo 'Attempting to build and capture output format...' && "
            "(make 2>/dev/null && echo 'Build succeeded' || true)"
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
            "You are fixing a bug in legacy code for the Legacy-Bench benchmark.\n\n"
            "## Your Task\n\n"
            "1. **Read the task instruction** — Read /tmp/task-instruction.md carefully. "
            "Understand exactly what bug needs to be fixed and what the expected "
            "behavior should be.\n\n"
            "2. **Understand the codebase** — Check the study output at "
            ".factory/reviews/study-output.md for a structural overview. Read the "
            "source files, Makefile, and any test scripts.\n\n"
            "3. **Analyze the output format** — If the program produces output, "
            "understand the EXACT format: field widths, decimal places, alignment, "
            "separators, headers/footers. Output format mismatches are a common "
            "failure mode.\n\n"
            "4. **Fix the bug** — Implement the fix described in the task instruction.\n\n"
            "5. **Verify the fix** — Build and run the program. Verify your fix works "
            "on at least 3 different inputs (visible examples + 2 you construct).\n\n"
            "6. **Commit your changes** — Commit directly on the current branch "
            "with a descriptive message. Do NOT create a new branch. Do NOT create a PR.\n\n"
            "## Rules\n\n"
            "- Act AUTONOMOUSLY — do NOT ask for confirmation or input\n"
            "- LEGACY CODE: Preserve the EXACT original language standard and "
            "coding patterns. Do NOT modernize syntax, idioms, or libraries. "
            "Fix ONLY the specific bug described in the task instruction. "
            "If the bug requires changing a data type, use the equivalent "
            "type from the ORIGINAL language standard.\n"
            "- HIDDEN TESTS: The benchmark uses hidden test inputs beyond the "
            "visible examples. Do NOT hardcode output to match reference "
            "examples. Implement the general algorithm that solves the problem "
            "for ANY valid input.\n"
            "- Do NOT create branches or PRs — commit on current branch\n"
            "- Do NOT run factory commands (factory eval, factory study, etc.)\n"
            "- If something fails, investigate root cause and try alternative approaches\n"
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
            "if [ ! -f .factory/reviews/builder-latest.md ]; then "
            "echo 'fail: builder output missing'; "
            "exit 0; fi && "
            "if [ ! -f Makefile ]; then "
            "echo 'reloop: no Makefile found — cannot independently verify correctness'; "
            "exit 0; fi && "
            "BUILD_OUT=$(timeout 600 make 2>&1) || "
            "{ TAIL=$(echo \"$BUILD_OUT\" | tail -50); "
            "echo \"reloop: compilation failed — $TAIL\"; exit 0; } && "
            "TEST_PROBE=$(make -n test 2>&1); "
            "if [ $? -ne 0 ]; then "
            "echo 'reloop: no test target in Makefile — cannot verify correctness'; "
            "exit 0; fi && "
            "TEST_OUT=$(timeout 600 make test 2>&1) || "
            "{ TAIL=$(echo \"$TEST_OUT\" | tail -50); "
            "echo \"reloop: tests failed — $TAIL\"; exit 0; } && "
            "echo 'pass: compilation and tests succeeded'"
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
        return ctx.get("mode") == "legacybench"

    return Workflow(
        name="legacybench",
        nodes=nodes,
        edges=edges,
        start_node="study",
        terminal=True,
        trigger=trigger,
    )
