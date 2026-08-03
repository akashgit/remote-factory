"""SaliTrap benchmark workflow — commonsense reasoning under salience bias.

4-node pipeline: study → solver → gate_verify → auto_merge
RELOOP from gate_verify back to solver (max 3 iterations) if answer file missing.

Designed for Harbor containers where:
- Task instruction is at /tmp/task-instruction.md (passed via --prompt)
- Task instruction contains a commonsense reasoning scenario with numerical distractors
- The agent must identify salience traps and reason about physical prerequisites
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
    "name": "salitrap",
    "description": (
        "SaliTrap benchmark mode — commonsense reasoning 4-node pipeline for "
        "identifying salience traps in reasoning scenarios with numerical distractors. "
        "study → solver → gate_verify → auto_merge with RELOOP on missing answer."
    ),
}


def workflow() -> Workflow:
    """Build the SaliTrap workflow from scratch."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 1: Study ──────────────────────────────────────────────
    nodes["study"] = FnNode(
        id="study",
        command=(
            "mkdir -p {project_path}/.factory/reviews && "
            "cd {project_path} && "
            "("
            "echo '=== Workspace Structure ===' && "
            "find . -type f | head -100 && "
            "echo '\\n=== Task Instruction ===' && "
            "cat /tmp/task-instruction.md 2>/dev/null || "
            "echo 'No task instruction file found at /tmp/task-instruction.md'"
            ") > .factory/reviews/study-output.md 2>&1"
        ),
        writes={".factory/reviews/study-output.md"},
    )

    # ── Node 2: Solver ─────────────────────────────────────────────
    nodes["solver"] = AgentNode(
        id="solver",
        role=AgentRole.BUILDER,
        model="opus",
        timeout=3600,
        max_iterations=3,
        prompt_template=(
            "You are solving a commonsense reasoning task for the SaliTrap "
            "benchmark. The task instruction describes a real-world scenario "
            "that may contain SALIENCE TRAPS — numerical details designed to "
            "distract you from fundamental physical, environmental, temporal, "
            "or rule-based constraints.\n\n"
            "## CRITICAL: Physics-Aware Reasoning\n\n"
            "Before engaging with ANY numerical optimization or calculation, "
            "you MUST first verify the physical prerequisites of the scenario:\n"
            "1. **Missing Prerequisites** — Does the scenario assume resources, "
            "tools, or conditions that are not actually present?\n"
            "2. **Environmental Mismatch** — Is the proposed action physically "
            "possible in the described environment?\n"
            "3. **Temporal/Physiological Violations** — Does the scenario "
            "require actions that violate biological limits or time constraints?\n"
            "4. **Rule Mismatches** — Does the scenario ignore regulations, "
            "social norms, or logical rules?\n\n"
            "If ANY prerequisite is violated, the correct answer is that the "
            "task is INFEASIBLE regardless of how optimal the numerical "
            "parameters might be. Do NOT be distracted by detailed numbers.\n\n"
            "## Your Task\n\n"
            "1. **Read the task instruction** — Read /tmp/task-instruction.md "
            "carefully. Identify the scenario and any embedded numerical "
            "distractors.\n\n"
            "2. **Check physical prerequisites FIRST** — Before any "
            "calculation, verify that the fundamental assumptions of the "
            "scenario are physically valid. Ask: 'Can this actually happen "
            "in the real world as described?'\n\n"
            "3. **Identify the trap dimension** — If a trap exists, classify "
            "it as one of: Missing Prerequisite, Environmental Mismatch, "
            "Temporal/Physiological Violation, or Rule Mismatch.\n\n"
            "4. **Write your answer** — Write a structured answer to "
            "/workspace/answer.txt containing:\n"
            "   - **Verdict:** feasible or infeasible\n"
            "   - **Trap type:** (if infeasible) which trap dimension applies\n"
            "   - **Reasoning:** step-by-step reasoning chain showing how "
            "you identified the trap or confirmed feasibility\n"
            "   - **Key insight:** the specific physical/environmental/"
            "temporal/rule constraint that makes this infeasible (or why "
            "all prerequisites are met)\n\n"
            "5. **Commit your answer** — Commit the answer file on the "
            "current branch.\n\n"
            "## Rules\n\n"
            "- Act AUTONOMOUSLY — do NOT ask for confirmation or input\n"
            "- ALWAYS check physical prerequisites before numerical reasoning\n"
            "- When in doubt about feasibility, lean toward INFEASIBLE — "
            "most scenarios in this benchmark contain hidden traps\n"
            "- Do NOT create branches or PRs — commit on current branch\n"
            "- Do NOT run factory commands (factory eval, factory study, etc.)\n"
            "- Do NOT optimize numerical parameters if prerequisites are "
            "violated — state the violation directly\n"
        ),
        reads={".factory/reviews/study-output.md"},
        writes={"/workspace/answer.txt"},
    )

    # ── Node 3: Gate Verify ────────────────────────────────────────
    nodes["gate_verify"] = GateNode(
        id="gate_verify",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            "if [ ! -f /workspace/answer.txt ]; then "
            "echo 'reloop: answer.txt not found at /workspace/answer.txt'; "
            "exit 0; fi && "
            "if [ ! -s /workspace/answer.txt ]; then "
            "echo 'reloop: answer.txt is empty'; "
            "exit 0; fi && "
            "CHANGES=$(git diff HEAD~1 --stat 2>/dev/null || echo 'NO_COMMITS') && "
            "if [ \"$CHANGES\" = 'NO_COMMITS' ] || [ -z \"$CHANGES\" ]; then "
            "echo 'reloop: no commits found — solver must commit answer.txt'; "
            "exit 0; fi && "
            "echo 'pass: answer.txt exists with content and changes committed'"
        ),
        reads={"/workspace/answer.txt"},
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
        reads={"/workspace/answer.txt"},
    )

    # ── Edges ──────────────────────────────────────────────────────

    edges = [
        Edge(source="study", target="solver"),
        Edge(source="solver", target="gate_verify"),
        Edge(source="gate_verify", target="auto_merge", condition=VerdictType.PROCEED),
        Edge(source="gate_verify", target="solver", condition=VerdictType.RELOOP),
    ]

    # ── Trigger ────────────────────────────────────────────────────

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "salitrap"

    return Workflow(
        name="salitrap",
        nodes=nodes,
        edges=edges,
        start_node="study",
        terminal=True,
        trigger=trigger,
    )
