"""ProgramBench benchmark workflow — adversarial discovery verification loop.

4-node loop: builder → reviewer → gate_verify → auto_merge
RELOOP from gate_verify back to builder (max 3 iterations) when the
reviewer finds incorrect or unexplored discoveries.

Designed for Harbor containers where:
- A compiled binary exists at /workspace/executable
- The builder probes, implements, AND maintains a structured discoveries file
- The reviewer adversarially validates each discovery against the ground truth
- Todos drive targeted fixes on RELOOP iterations
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
        "ProgramBench benchmark mode — adversarial discovery verification "
        "loop. builder → reviewer → gate_verify → auto_merge "
        "with RELOOP on unverified discoveries."
    ),
}


def workflow() -> Workflow:
    """Build the ProgramBench workflow — adversarial discovery verification."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 1: Builder ──────────────────────────────────────────
    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        model="opus",
        timeout=1200,
        max_iterations=3,
        prompt_template=(
            "You are reverse-engineering a compiled binary and producing "
            "equivalent source code for the ProgramBench benchmark.\n\n"
            "## Your Task\n\n"
            "1. **Read the task instruction** — Read /tmp/task-instruction.md "
            "for context on what the binary does.\n\n"
            "2. **Back up the original binary** — Run: "
            "cp /workspace/executable /workspace/executable.bak\n"
            "   (Skip if executable.bak already exists from a previous "
            "iteration.)\n\n"
            "3. **Check for TODOs from a previous review** — If "
            "/workspace/todos.md exists, read it and address EACH item "
            "before doing anything else. These are specific issues found by "
            "the reviewer that MUST be fixed. Update /workspace/discoveries.md "
            "with corrected evidence as you fix each TODO.\n\n"
            "4. **Probe the binary systematically** — Run the binary with:\n"
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
            "5. **Maintain the discoveries file** — For EVERY behavioral "
            "discovery (flag behavior, output format, edge case, error "
            "message, exit code, etc.), add an entry to "
            "/workspace/discoveries.md with this format:\n\n"
            "   ```markdown\n"
            "   ## Discovery: <short title>\n"
            "   - **What:** <what was discovered>\n"
            "   - **Evidence:** <command run and output observed>\n"
            "   - **Status:** verified | uncertain | unexplored\n"
            "   - **Notes:** <any additional context>\n"
            "   ```\n\n"
            "   Record EVERY discovery, not just the ones you're confident "
            "about. Mark discoveries as 'uncertain' if you're not 100%% sure. "
            "Mark discoveries as 'unexplored' if you found something but "
            "didn't dig into it yet.\n\n"
            "6. **Read any documentation** — Check /workspace/ for README.md, "
            "man pages, or other docs.\n\n"
            "7. **Write the source code** — Implement C source code that "
            "reproduces ALL discovered behaviors:\n"
            "   - Match every flag and option exactly\n"
            "   - Match output format exactly (spacing, newlines, field widths)\n"
            "   - Match exit codes exactly\n"
            "   - Match error messages exactly\n"
            "   - CRITICAL: Hardcode the exact version string from -V output. "
            "Do NOT use __DATE__ or __TIME__ macros — these produce different "
            "values on every build and will fail verification.\n\n"
            "8. **Create compile.sh** — Write a build script that:\n"
            "   - Compiles the source to /workspace/executable\n"
            "   - Is executable (chmod +x)\n\n"
            "9. **Test by diffing** — After compiling, test your build "
            "against executable.bak by running the same commands on both and "
            "comparing outputs. Fix any mismatches.\n\n"
            "10. **Commit your changes** — Commit directly on the current "
            "branch with a descriptive message.\n\n"
            "## Rules\n\n"
            "- Act AUTONOMOUSLY — do NOT ask for confirmation or input\n"
            "- Record EVERY discovery, not just the ones you're confident "
            "about\n"
            "- Mark discoveries as 'uncertain' if you're not 100%% sure\n"
            "- Mark discoveries as 'unexplored' if you found something but "
            "didn't dig into it\n"
            "- Do NOT skip the discoveries file — it is required\n"
            "- Do NOT use __DATE__, __TIME__, or other non-deterministic "
            "macros\n"
            "- Do NOT create branches or PRs — commit on current branch\n"
            "- Do NOT run factory commands (factory eval, factory study, etc.)\n"
        ),
        reads=set(),
        writes={"/workspace/discoveries.md"},
    )

    # ── Node 2: Reviewer ─────────────────────────────────────────
    nodes["reviewer"] = AgentNode(
        id="reviewer",
        role=AgentRole.RESEARCHER,
        model="opus",
        timeout=900,
        max_iterations=1,
        prompt_template=(
            "You are an adversarial reviewer for the ProgramBench benchmark. "
            "A builder agent has probed a compiled binary, implemented source "
            "code, and recorded its discoveries. Your job is to validate each "
            "discovery against the ground truth binary and catch "
            "overconfidence and missed exploration.\n\n"
            "## Your Task\n\n"
            "1. **Read the discoveries** — Read /workspace/discoveries.md "
            "to see what the builder found and claims to have implemented.\n\n"
            "2. **Validate each discovery** — For EACH discovery entry:\n"
            "   a. Independently run the relevant command against "
            "/workspace/executable.bak (the ground truth binary)\n"
            "   b. Run the same command against /workspace/executable "
            "(the builder's version)\n"
            "   c. Compare the outputs character-by-character, including "
            "whitespace, newlines, and exit codes\n"
            "   d. Classify the discovery:\n"
            "      - **verified**: the builder's implementation matches the "
            "original binary for this behavior\n"
            "      - **incorrect**: the builder thinks it works but the "
            "outputs differ\n"
            "      - **unexplored**: the builder noted this but didn't fully "
            "implement or test it\n\n"
            "3. **Write the review** — Save your review to "
            "/workspace/review.md with classifications for each discovery. "
            "Include the exact commands you ran and the outputs you "
            "observed.\n\n"
            "4. **Write TODOs if needed** — If ANY discoveries are "
            "'incorrect' or 'unexplored', write /workspace/todos.md with "
            "specific tasks:\n\n"
            "   ```markdown\n"
            "   ## TODO: <title>\n"
            "   - **Discovery:** <reference to the discovery>\n"
            "   - **Problem:** <what's wrong or what needs exploration>\n"
            "   - **Expected:** <what executable.bak actually outputs>\n"
            "   - **Actual:** <what the builder's version outputs>\n"
            "   - **Action:** <specific thing the builder needs to fix>\n"
            "   ```\n\n"
            "   If ALL discoveries are 'verified', write an empty "
            "/workspace/todos.md (or don't create it).\n\n"
            "5. **Probe for unknown unknowns** — Run a few ADDITIONAL test "
            "cases against executable.bak that the builder didn't think of. "
            "Try:\n"
            "   - Edge cases: empty input, very long input, binary input, "
            "special characters\n"
            "   - Flag combinations the builder didn't try\n"
            "   - Uncommon but valid invocations\n"
            "   - Boundary values for numeric arguments\n"
            "   If any reveal NEW behaviors not in discoveries.md, add them "
            "as 'unexplored' TODOs in /workspace/todos.md.\n\n"
            "## Rules\n\n"
            "- Act AUTONOMOUSLY — do NOT ask for confirmation or input\n"
            "- Be ADVERSARIAL — assume the builder is overconfident\n"
            "- Compare outputs EXACTLY — even minor whitespace differences "
            "matter\n"
            "- Always compare exit codes, not just stdout\n"
            "- Do NOT fix the code yourself — only document issues for the "
            "builder\n"
            "- Do NOT create branches or PRs\n"
            "- Do NOT run factory commands\n"
        ),
        reads={"/workspace/discoveries.md"},
        writes={"/workspace/review.md", "/workspace/todos.md"},
    )

    # ── Node 3: Gate Verify ──────────────────────────────────────
    nodes["gate_verify"] = GateNode(
        id="gate_verify",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            "if [ ! -f /workspace/todos.md ]; then "
            "echo 'pass: no todos file, all discoveries verified'; "
            "elif [ ! -s /workspace/todos.md ]; then "
            "echo 'pass: todos file is empty, all discoveries verified'; "
            "elif grep -q '## TODO' /workspace/todos.md; then "
            "echo 'reloop: todos remain to be addressed'; "
            "else "
            "echo 'pass: no todo items found'; "
            "fi"
        ),
        reads={"/workspace/todos.md"},
    )

    # ── Node 4: Auto Merge ───────────────────────────────────────
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
        reads={"/workspace/review.md"},
    )

    # ── Edges ────────────────────────────────────────────────────

    edges = [
        Edge(source="builder", target="reviewer"),
        Edge(source="reviewer", target="gate_verify"),
        Edge(source="gate_verify", target="auto_merge", condition=VerdictType.PROCEED),
        Edge(source="gate_verify", target="builder", condition=VerdictType.RELOOP),
    ]

    # ── Trigger ──────────────────────────────────────────────────

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "programbench"

    return Workflow(
        name="programbench",
        nodes=nodes,
        edges=edges,
        start_node="builder",
        terminal=True,
        trigger=trigger,
    )
