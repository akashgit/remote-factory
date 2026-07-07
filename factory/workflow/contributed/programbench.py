"""ProgramBench benchmark workflow — discovery-first reverse engineering pipeline.

5-node pipeline: discover → plan → builder → gate_verify → auto_merge
RELOOP from gate_verify back to builder (max 3 iterations) on failure.

Designed for Harbor containers where:
- A compiled binary exists at /workspace/executable
- The agent must reverse-engineer its behavior and produce equivalent source
- Discovery phase probes the binary exhaustively before any code is written
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
        "ProgramBench benchmark mode — discovery-first reverse engineering "
        "pipeline. discover → plan → builder → gate_verify → auto_merge "
        "with RELOOP on failure."
    ),
}


def workflow() -> Workflow:
    """Build the ProgramBench workflow — discovery-first reverse engineering."""
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
            "compiled binary at /workspace/executable and document EVERYTHING "
            "you discover about its behavior.\n\n"
            "## Your Task\n\n"
            "1. **Read the task instruction** — Read /tmp/task-instruction.md "
            "for context on what the binary does.\n\n"
            "2. **Check the workspace** — List all files in /workspace/. Look "
            "for README files, man pages, documentation, data files, or any "
            "other clues about the binary's purpose.\n\n"
            "3. **Read any documentation found** — If there is a README.md or "
            "other docs, read them thoroughly.\n\n"
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
            "   - Provide sample files as arguments\n\n"
            "5. **Record exact outputs** — For EVERY invocation, capture:\n"
            "   - The exact command run\n"
            "   - stdout (exact text)\n"
            "   - stderr (exact text)\n"
            "   - Exit code ($?)\n\n"
            "6. **Note special behaviors** — Document:\n"
            "   - The exact version string from -V or --version output\n"
            "   - How the binary behaves with no terminal (e.g. 'Error opening "
            "terminal' messages)\n"
            "   - Any environment variables it reads\n"
            "   - Any files it creates, reads, or modifies\n"
            "   - Interactive vs non-interactive behavior differences\n\n"
            "7. **Write ALL findings** — Save your complete findings to "
            ".factory/reviews/discovery.md. Be EXHAUSTIVE — the builder agent "
            "will use this as its sole reference for implementation.\n\n"
            "## Rules\n\n"
            "- Act AUTONOMOUSLY — do NOT ask for confirmation or input\n"
            "- Be THOROUGH — try every flag combination you can think of\n"
            "- Record EXACT outputs, not summaries\n"
            "- Note exit codes for each invocation\n"
            "- The binary is execute-only — you cannot read its contents\n"
            "- Do NOT attempt to implement anything — only discover and document\n"
            "- Do NOT run factory commands\n"
            "- Do NOT create branches or PRs\n"
        ),
        reads=set(),
        writes={".factory/reviews/discovery.md"},
    )

    # ── Node 2: Plan ──────────────────────────────────────────────
    nodes["plan"] = FnNode(
        id="plan",
        command=(
            "cd {project_path} && "
            "echo 'pass: discovery complete'"
        ),
        reads={".factory/reviews/discovery.md"},
        writes=set(),
    )

    # ── Node 3: Builder ───────────────────────────────────────────
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
            "1. **Read the discovery findings** — Read "
            ".factory/reviews/discovery.md thoroughly. This contains the "
            "complete behavioral profile of the target binary gathered by "
            "a discovery agent. It has exact outputs, exit codes, and flag "
            "behaviors for every invocation tested.\n\n"
            "2. **Read any documentation** — Check /workspace/ for README.md, "
            "man pages, or other docs. Read /tmp/task-instruction.md for the "
            "task description.\n\n"
            "3. **Write the source code** — Implement C source code that "
            "reproduces ALL discovered behaviors:\n"
            "   - Match every flag and option exactly\n"
            "   - Match output format exactly (spacing, newlines, field widths)\n"
            "   - Match exit codes exactly\n"
            "   - Match error messages exactly\n"
            "   - CRITICAL: Hardcode the exact version string from -V output. "
            "Do NOT use __DATE__ or __TIME__ macros — these produce different "
            "values on every build and will fail verification.\n\n"
            "4. **Create compile.sh** — Write a build script that:\n"
            "   - Backs up the original: cp /workspace/executable "
            "/workspace/executable.bak\n"
            "   - Compiles the source to /workspace/executable\n"
            "   - Is executable (chmod +x)\n\n"
            "5. **Differential testing** — After compiling, compare your "
            "implementation against the original:\n"
            "   - Run both /workspace/executable.bak and /workspace/executable "
            "with the same flags\n"
            "   - Compare stdout, stderr, and exit codes\n"
            "   - Fix ANY differences found\n"
            "   - Test with at least 10 different flag combinations\n\n"
            "6. **Commit your changes** — Commit directly on the current "
            "branch with a descriptive message. Do NOT create a new branch. "
            "Do NOT create a PR.\n\n"
            "## Rules\n\n"
            "- Act AUTONOMOUSLY — do NOT ask for confirmation or input\n"
            "- The discovery findings are your primary reference — trust them\n"
            "- Match behavior EXACTLY — even minor output differences cause "
            "verification failure\n"
            "- Do NOT use __DATE__, __TIME__, or other non-deterministic macros\n"
            "- Do NOT create branches or PRs — commit on current branch\n"
            "- Do NOT run factory commands (factory eval, factory study, etc.)\n"
            "- If differential testing reveals mismatches, fix them before "
            "committing\n"
        ),
        reads={".factory/reviews/discovery.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    # ── Node 4: Gate Verify ───────────────────────────────────────
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
            "echo 'pass: compile.sh succeeded and executable exists'"
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # ── Node 5: Auto Merge ────────────────────────────────────────
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
        Edge(source="discover", target="plan"),
        Edge(source="plan", target="builder"),
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
