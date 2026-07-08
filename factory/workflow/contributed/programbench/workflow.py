"""ProgramBench benchmark workflow — scaffold-first reverse engineering pipeline.

4-node pipeline: discover → builder → gate_verify → auto_merge
RELOOP from gate_verify back to builder (max 3 iterations) on failure.

Designed for Harbor containers where:
- A compiled binary exists at /workspace/executable
- The discovery agent probes the binary AND builds a test harness
- The builder uses the test scaffold as a tight feedback loop
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
    "name": "programbench",
    "description": (
        "ProgramBench benchmark mode — scaffold-first reverse engineering "
        "pipeline. discover → builder → gate_verify → auto_merge "
        "with RELOOP on failure."
    ),
}


def workflow() -> Workflow:
    """Build the ProgramBench workflow — scaffold-first reverse engineering."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 1: Discover ──────────────────────────────────────────
    nodes["discover"] = AgentNode(
        id="discover",
        role=AgentRole.RESEARCHER,
        model="opus",
        timeout=900,
        max_iterations=1,
        prompt_template=(
            "You are a reverse-engineering researcher. Your job is to probe a "
            "compiled binary at /workspace/executable, document its behavior, "
            "AND build a test harness that the builder agent will compile "
            "against.\n\n"
            "## Your Task\n\n"
            "1. **Read the task instruction** — Read /tmp/task-instruction.md "
            "for context on what the binary does.\n\n"
            "2. **Check the workspace** — List all files in /workspace/. Look "
            "for README files, man pages, documentation, data files, or any "
            "other clues about the binary's purpose.\n\n"
            "3. **Read any documentation found** — If there is a README.md or "
            "other docs, read them thoroughly.\n\n"
            "4. **Back up the original binary** — Run: "
            "cp /workspace/executable /workspace/executable.bak\n\n"
            "5. **Probe the binary systematically** — Run the binary with:\n"
            "   - No arguments\n"
            "   - --help, -h\n"
            "   - --version, -V, -v\n"
            "   - Invalid/unknown flags to see error messages\n"
            "   - Single-letter flags: -a through -z, -A through -Z\n"
            "   - Common long flags: --verbose, --debug, --output, --input, "
            "--format, --config, --list, --all, --recursive, --quiet\n"
            "   - Flags that take arguments — try them with various values\n"
            "   - Pipe input via stdin\n"
            "   - Provide sample files as arguments\n"
            "   - Combinations of flags\n\n"
            "6. **Build the test scaffold** — For EACH behavior discovered, "
            "create a test case. Create these files:\n\n"
            "   **`/workspace/tests/test_behavior.sh`** — a shell script that "
            "tests the agent's build against the original binary. Structure:\n"
            "   ```bash\n"
            "   #!/bin/bash\n"
            "   PASS=0; FAIL=0; TOTAL=0\n"
            "   run_test() {\n"
            '       name=$1; shift\n'
            "       TOTAL=$((TOTAL+1))\n"
            '       expected=$(/workspace/executable.bak "$@" 2>&1)\n'
            "       expected_exit=$?\n"
            '       actual=$(/workspace/executable "$@" 2>&1)\n'
            "       actual_exit=$?\n"
            '       if [ "$expected" = "$actual" ] && '
            '[ "$expected_exit" = "$actual_exit" ]; then\n'
            "           PASS=$((PASS+1))\n"
            "       else\n"
            "           FAIL=$((FAIL+1))\n"
            '           echo "FAIL: $name"\n'
            '           if [ "$expected" != "$actual" ]; then\n'
            '               diff <(echo "$expected") <(echo "$actual") '
            "| head -10\n"
            "           fi\n"
            '           if [ "$expected_exit" != "$actual_exit" ]; then\n'
            '               echo "  exit code: expected=$expected_exit '
            'actual=$actual_exit"\n'
            "           fi\n"
            "       fi\n"
            "   }\n"
            "   # Tests for stdin input:\n"
            "   run_test_stdin() {\n"
            '       name=$1; input=$2; shift 2\n'
            "       TOTAL=$((TOTAL+1))\n"
            '       expected=$(echo "$input" | '
            '/workspace/executable.bak "$@" 2>&1)\n'
            "       expected_exit=$?\n"
            '       actual=$(echo "$input" | '
            '/workspace/executable "$@" 2>&1)\n'
            "       actual_exit=$?\n"
            '       if [ "$expected" = "$actual" ] && '
            '[ "$expected_exit" = "$actual_exit" ]; then\n'
            "           PASS=$((PASS+1))\n"
            "       else\n"
            "           FAIL=$((FAIL+1))\n"
            '           echo "FAIL: $name"\n'
            '           if [ "$expected" != "$actual" ]; then\n'
            '               diff <(echo "$expected") <(echo "$actual") '
            "| head -10\n"
            "           fi\n"
            "       fi\n"
            "   }\n"
            "   # Tests for file input:\n"
            "   run_test_file() {\n"
            '       name=$1; file=$2; shift 2\n'
            "       TOTAL=$((TOTAL+1))\n"
            '       expected=$(/workspace/executable.bak "$@" "$file" 2>&1)\n'
            "       expected_exit=$?\n"
            '       actual=$(/workspace/executable "$@" "$file" 2>&1)\n'
            "       actual_exit=$?\n"
            '       if [ "$expected" = "$actual" ] && '
            '[ "$expected_exit" = "$actual_exit" ]; then\n'
            "           PASS=$((PASS+1))\n"
            "       else\n"
            "           FAIL=$((FAIL+1))\n"
            '           echo "FAIL: $name"\n'
            '           if [ "$expected" != "$actual" ]; then\n'
            '               diff <(echo "$expected") <(echo "$actual") '
            "| head -10\n"
            "           fi\n"
            "       fi\n"
            "   }\n"
            '   run_test "help_flag" --help\n'
            '   run_test "version_flag" --version\n'
            "   # ... add more test cases for every behavior discovered ...\n"
            '   echo "$PASS/$TOTAL passed, $FAIL failed"\n'
            "   ```\n\n"
            "   **`/workspace/tests/expected/`** — directory of expected output "
            "files (one per test case) for reference.\n\n"
            "   Create test data files as needed (CSV, TSV, edge-case inputs) "
            "in /workspace/tests/.\n\n"
            "7. **Make the test script executable** — chmod +x "
            "/workspace/tests/test_behavior.sh\n\n"
            "8. **Write a summary** — Save your complete findings to "
            ".factory/reviews/discovery.md. Include:\n"
            "   - Binary purpose and capabilities\n"
            "   - All flags and options discovered\n"
            "   - Exact version strings\n"
            "   - Key behavioral notes (error handling, edge cases, output "
            "formatting)\n"
            "   - List of all test cases created\n\n"
            "9. **Commit the test scaffold** — git add and commit the test "
            "files and discovery notes.\n\n"
            "## Rules\n\n"
            "- Act AUTONOMOUSLY — do NOT ask for confirmation or input\n"
            "- Be THOROUGH — try every flag combination you can think of\n"
            "- Record EXACT outputs, not summaries\n"
            "- Note exit codes for each invocation\n"
            "- The binary is execute-only — you cannot read its contents\n"
            "- Do NOT attempt to implement the binary — only discover and "
            "build tests\n"
            "- Do NOT run factory commands\n"
            "- Do NOT create branches or PRs\n"
            "- The test scaffold is your PRIMARY deliverable — the builder "
            "will iterate against it\n"
        ),
        reads=set(),
        writes={
            ".factory/reviews/discovery.md",
            "/workspace/tests/test_behavior.sh",
        },
    )

    # ── Node 2: Builder ───────────────────────────────────────────
    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        model="opus",
        timeout=1200,
        max_iterations=3,
        prompt_template=(
            "You are reverse-engineering a compiled binary and producing "
            "equivalent source code for the ProgramBench benchmark. A "
            "discovery agent has already probed the binary and built a test "
            "harness for you.\n\n"
            "## Your Task\n\n"
            "1. **Read the discovery findings** — Read "
            ".factory/reviews/discovery.md for the behavioral summary.\n\n"
            "2. **Read the test scaffold** — Read "
            "/workspace/tests/test_behavior.sh to understand what tests "
            "exist and what behaviors are expected.\n\n"
            "3. **Read any documentation** — Check /workspace/ for README.md, "
            "man pages, or other docs. Read /tmp/task-instruction.md for the "
            "task description.\n\n"
            "4. **Examine the original binary** — Run /workspace/executable.bak "
            "directly if you need to clarify any behavior not covered by the "
            "test scaffold.\n\n"
            "5. **Write the source code** — Implement C source code that "
            "reproduces ALL discovered behaviors:\n"
            "   - Match every flag and option exactly\n"
            "   - Match output format exactly (spacing, newlines, field widths)\n"
            "   - Match exit codes exactly\n"
            "   - Match error messages exactly\n"
            "   - CRITICAL: Hardcode the exact version string from -V output. "
            "Do NOT use __DATE__ or __TIME__ macros — these produce different "
            "values on every build and will fail verification.\n\n"
            "6. **Create compile.sh** — Write a build script that:\n"
            "   - Compiles the source to /workspace/executable\n"
            "   - Is executable (chmod +x)\n\n"
            "7. **Run the test scaffold** — After compiling, run:\n"
            "   cd /workspace && bash tests/test_behavior.sh\n"
            "   This tests your build against the original binary.\n\n"
            "8. **ITERATE** — Fix failures, recompile, re-test. Run the "
            "test scaffold FREQUENTLY during development, not just at the "
            "end. The test scaffold is your spec — make all tests pass.\n\n"
            "9. **Commit your changes** — Commit directly on the current "
            "branch with a descriptive message. Do NOT create a new branch. "
            "Do NOT create a PR.\n\n"
            "## Rules\n\n"
            "- Act AUTONOMOUSLY — do NOT ask for confirmation or input\n"
            "- The test scaffold is your primary spec — make all tests pass\n"
            "- The discovery findings are your reference — trust them\n"
            "- Match behavior EXACTLY — even minor output differences cause "
            "verification failure\n"
            "- Do NOT use __DATE__, __TIME__, or other non-deterministic macros\n"
            "- Do NOT create branches or PRs — commit on current branch\n"
            "- Do NOT run factory commands (factory eval, factory study, etc.)\n"
            "- Run the test scaffold FREQUENTLY during development\n"
            "- If tests reveal mismatches, fix them before committing\n"
        ),
        reads={
            ".factory/reviews/discovery.md",
            "/workspace/tests/test_behavior.sh",
        },
        writes={".factory/reviews/builder-latest.md"},
    )

    # ── Node 3: Gate Verify ───────────────────────────────────────
    nodes["gate_verify"] = GateNode(
        id="gate_verify",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            "if ! test -f compile.sh; then "
            "echo 'reloop: compile.sh missing'; "
            "exit 0; fi && "
            "if ! bash compile.sh 2>&1; then "
            "echo 'reloop: compile.sh failed'; "
            "exit 0; fi && "
            "if ! test -f /workspace/executable; then "
            "echo 'reloop: /workspace/executable not found after compilation'; "
            "exit 0; fi && "
            "if [ -f /workspace/tests/test_behavior.sh ]; then "
            "cd /workspace && "
            "RESULT=$(bash tests/test_behavior.sh 2>&1 | tail -1) && "
            "echo \"$RESULT\" && "
            "if echo \"$RESULT\" | grep -q '0 failed'; then "
            "echo 'pass: all behavioral tests pass'; "
            "else "
            "echo \"reloop: $RESULT\"; "
            "fi; "
            "else "
            "echo 'pass: compile.sh succeeded, no test scaffold found'; "
            "fi"
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # ── Node 4: Auto Merge ────────────────────────────────────────
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

    # ── Edges ─────────────────────────────────────────────────────

    edges = [
        Edge(source="discover", target="builder"),
        Edge(source="builder", target="gate_verify"),
        Edge(source="gate_verify", target="auto_merge", condition=VerdictType.PROCEED),
        Edge(source="gate_verify", target="builder", condition=VerdictType.RELOOP),
    ]

    # ── Trigger ───────────────────────────────────────────────────

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "programbench"

    return Workflow(
        name="programbench",
        nodes=nodes,
        edges=edges,
        start_node="discover",
        terminal=True,
        trigger=trigger,
    )
