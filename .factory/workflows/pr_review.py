"""PR review workflow — root-cause analysis and solution validation for pull requests.

5-node linear pipeline: fetch_pr → researcher_pr → reviewer_pr → gate_review → finalize_pr
RELOOP from gate_review back to reviewer_pr (max 2 iterations) on quality failure.

Invoked via: factory pr-review --focus <PR_NUMBER>
Output: .factory/reviews/pr-review.md
"""

from __future__ import annotations

from typing import Any

from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ArtifactCheck,
    Edge,
    FnNode,
    GateNode,
    ProjectState,
    VerdictType,
    Workflow,
)

meta = {
    "name": "pr-review",
    "description": (
        "PR review mode — fetches a PR diff and linked issue via gh CLI, "
        "then reviews with two criteria: (1) does it fix the root cause or "
        "just a symptom, (2) does it actually solve the intended problem. "
        "Outputs a structured review at .factory/reviews/pr-review.md."
    ),
    "argument_hint": "<project_path> --focus <PR_NUMBER>",
}


def workflow() -> Workflow:
    """Build the pr-review workflow — linear pipeline with quality gate loop."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 1: Fetch PR data ─────────────────────────────────────
    nodes["fetch_pr"] = FnNode(
        id="fetch_pr",
        command=(
            'mkdir -p {project_path}/.factory/reviews && '
            'cd {project_path} && '
            '('
            'echo "# PR Context for Review" && '
            'echo "" && '
            'echo "## PR Metadata" && '
            'gh pr view {focus} --json number,title,body,author,baseRefName,headRefName,files,url,additions,deletions,labels 2>/dev/null && '
            'echo "" && '
            'echo "## PR Diff" && '
            'echo "\\`\\`\\`diff" && '
            'gh pr diff {focus} 2>/dev/null && '
            'echo "\\`\\`\\`" && '
            'echo "" && '
            'echo "## Linked Issues" && '
            'for issue_num in $(gh pr view {focus} --json body --jq ".body" 2>/dev/null '
            '| grep -oE "(close[sd]?|fix(e[sd])?|resolve[sd]?)\\s*#[0-9]+" -i '
            '| grep -oE "[0-9]+"); do '
            'echo "### Issue #$issue_num" && '
            'gh issue view "$issue_num" --json title,body,labels,comments 2>/dev/null && '
            'echo ""; '
            'done'
            ') > .factory/reviews/pr-context.md 2>&1'
        ),
        notes=(
            "Fetch PR metadata, full diff, and linked issue text via gh CLI. "
            "Extracts issue references from PR body using closes/fixes/resolves patterns. "
            "All output consolidated into pr-context.md for downstream agents."
        ),
        writes={".factory/reviews/pr-context.md"},
    )

    # ── Node 2: Research PR context ───────────────────────────────
    nodes["researcher_pr"] = AgentNode(
        id="researcher_pr",
        role=AgentRole.RESEARCHER,
        model="sonnet",
        timeout=600,
        prompt_template=(
            "You are reviewing a pull request. Read the PR context at "
            ".factory/reviews/pr-context.md.\n\n"
            "Your task:\n"
            "1. Parse the PR diff and identify every file changed, with a summary "
            "of what each change does\n"
            "2. Read the linked issue(s) and summarize the problem being solved\n"
            "3. Identify the code areas surrounding the changes — read the full "
            "source files that were modified to understand the broader context\n"
            "4. Note any related modules, imports, or dependencies affected\n"
            "5. Identify what the ROOT CAUSE of the reported issue is (from "
            "reading the issue description and the surrounding code)\n"
            "6. Note whether the PR changes tests, and if so, what they verify\n\n"
            "Write your analysis to .factory/reviews/pr-research.md with these "
            "sections:\n"
            "## Issue Summary\n"
            "## Root Cause Hypothesis\n"
            "## Changed Files Analysis\n"
            "## Surrounding Context\n"
            "## Test Changes\n"
        ),
        reads={".factory/reviews/pr-context.md"},
        writes={".factory/reviews/pr-research.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/reviews/pr-research.md",
                must_exist=True,
                min_size=200,
                must_contain=["## Changed Files Analysis", "## Issue Summary"],
            )
        ],
    )

    # ── Node 3: Review (root-cause + solution validation) ─────────
    nodes["reviewer_pr"] = AgentNode(
        id="reviewer_pr",
        role=AgentRole.CODE_REVIEWER,
        model="opus",
        timeout=900,
        prompt_template=(
            "You are a specialist code reviewer focused on root-cause analysis "
            "and solution validation.\n\n"
            "Read the PR context at .factory/reviews/pr-context.md and the "
            "research analysis at .factory/reviews/pr-research.md.\n\n"
            "Perform TWO analyses:\n\n"
            "### Analysis 1: Root Cause vs Symptom\n"
            "Determine whether this PR fixes the ROOT CAUSE of the issue or "
            "just treats SYMPTOMS. Look for these symptom-fix patterns:\n"
            "- Adding try/except around a crash without fixing why it crashes\n"
            "- Adding null checks without fixing why the value is null\n"
            "- Adding retries without fixing why the operation fails\n"
            "- Hardcoding values that should be computed\n"
            "- Suppressing errors/warnings instead of resolving them\n"
            "- Adding special-case handling that should be a general fix\n\n"
            "### Analysis 2: Solution Validation\n"
            "Determine whether this PR actually SOLVES the stated problem. "
            "Check:\n"
            "- Does the change address every aspect of the issue description?\n"
            "- Are there edge cases the fix doesn't handle?\n"
            "- Could the fix introduce new problems?\n"
            "- Do the tests (if any) actually verify the fix works?\n"
            "- Is the fix complete, or does it need follow-up work?\n\n"
            "Write your review to .factory/reviews/reviewer-draft.md with "
            "EXACTLY this structure:\n\n"
            "# PR Review: #<number> - <title>\n\n"
            "## Summary\n"
            "Brief overview of PR intent and scope.\n\n"
            "## Root Cause Analysis\n"
            "- **Finding:** Root cause vs symptom assessment\n"
            "- **Evidence:** Specific code locations and reasoning\n"
            "- **Rating:** One of: ✅ Addresses root cause | ⚠️ Partial | "
            "❌ Symptom-only fix\n\n"
            "## Solution Validation\n"
            "- **Finding:** Does it solve the stated problem?\n"
            "- **Evidence:** How the changes map to the issue requirements\n"
            "- **Rating:** One of: ✅ Solves problem | ⚠️ Partial solution | "
            "❌ Does not solve\n\n"
            "## Recommendations\n"
            "Specific, actionable improvements (if any). Reference exact files "
            "and line ranges.\n\n"
            "## Approval Status\n"
            "One of: ✅ Approve | ⚠️ Approve with comments | ❌ Request changes\n"
        ),
        reads={".factory/reviews/pr-context.md", ".factory/reviews/pr-research.md"},
        writes={".factory/reviews/reviewer-draft.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/reviews/reviewer-draft.md",
                must_exist=True,
                min_size=300,
                must_contain=[
                    "## Root Cause Analysis",
                    "## Solution Validation",
                    "## Approval Status",
                ],
            )
        ],
    )

    # ── Node 4: Quality gate ──────────────────────────────────────
    nodes["gate_review"] = GateNode(
        id="gate_review",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        max_iterations=2,
        gate_prompt=(
            "Evaluate the PR review draft at .factory/reviews/reviewer-draft.md "
            "against these quality criteria:\n\n"
            "1. **Root Cause Analysis present:** The review has a Root Cause "
            "Analysis section with a Finding, Evidence, and Rating.\n"
            "2. **Solution Validation present:** The review has a Solution "
            "Validation section with a Finding, Evidence, and Rating.\n"
            "3. **Evidence-based:** The review references specific code "
            "locations (file names, function names, or line references) — "
            "not just opinions.\n"
            "4. **Actionable:** If recommendations exist, they are concrete "
            "and specific (not vague advice like 'consider improving').\n"
            "5. **Balanced:** The review acknowledges what the PR does well, "
            "not just criticism.\n\n"
            "PROCEED if all 5 criteria are met.\n"
            "RELOOP to reviewer_pr with specific feedback if any criterion "
            "fails. State which criteria failed and why.\n"
            "HALT only if the review is fundamentally broken (e.g., empty, "
            "irrelevant, or reviews the wrong PR)."
        ),
        reads={".factory/reviews/reviewer-draft.md", ".factory/reviews/pr-research.md"},
    )

    # ── Node 5: Finalize ──────────────────────────────────────────
    nodes["finalize_pr"] = AgentNode(
        id="finalize_pr",
        role=AgentRole.ARCHIVIST,
        model="haiku",
        timeout=300,
        blocking=True,
        prompt_template=(
            "Read the approved PR review draft at "
            ".factory/reviews/reviewer-draft.md.\n\n"
            "Copy its content to .factory/reviews/pr-review.md, preserving "
            "the exact structure. Make only minimal formatting improvements:\n"
            "- Ensure markdown headers are properly formatted\n"
            "- Ensure rating emojis (✅/⚠️/❌) are present\n"
            "- Add a timestamp line at the top: 'Reviewed: YYYY-MM-DD'\n"
            "- Do NOT change any findings, evidence, ratings, or recommendations\n\n"
            "Write the final review to .factory/reviews/pr-review.md."
        ),
        reads={".factory/reviews/reviewer-draft.md"},
        writes={".factory/reviews/pr-review.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/reviews/pr-review.md",
                must_exist=True,
                min_size=200,
                must_contain=["## Root Cause Analysis", "## Solution Validation"],
            )
        ],
    )

    # ── Edges ─────────────────────────────────────────────────────
    edges = [
        # Linear flow
        Edge(source="fetch_pr", target="researcher_pr"),
        Edge(source="researcher_pr", target="reviewer_pr"),
        Edge(source="reviewer_pr", target="gate_review"),
        # Gate verdicts
        Edge(source="gate_review", target="finalize_pr", condition=VerdictType.PROCEED),
        Edge(source="gate_review", target="reviewer_pr", condition=VerdictType.RELOOP),
        Edge(source="gate_review", target="finalize_pr", condition=VerdictType.HALT),
    ]

    # ── Trigger ───────────────────────────────────────────────────
    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        """Trigger when mode is 'pr-review' and --focus contains a PR number."""
        if ctx.get("mode") != "pr-review":
            return False
        focus = ctx.get("focus")
        if focus is None:
            return False
        try:
            pr_num = int(str(focus).strip().lstrip("#"))
            return pr_num > 0
        except (ValueError, TypeError):
            return False

    return Workflow(
        name="pr-review",
        nodes=nodes,
        edges=edges,
        start_node="fetch_pr",
        terminal=True,
        trigger=trigger,
    )
