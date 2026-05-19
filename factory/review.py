"""PR review formatting and posting — posts structured reviews on GitHub/GitLab PRs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import structlog

from factory.forge import ForgeOps
from factory.issue import Forge

log = structlog.get_logger()


@dataclass
class ReviewPayload:
    """Structured review data ready for formatting."""

    verdict: str  # "KEEP" or "REVERT"
    reason: str
    score_before: float | None
    score_after: float | None
    threshold: float
    guard_results: dict[str, str]  # {check_name: "PASS" | "FAIL"}
    precheck_summary: str
    code_notes: list[str]
    experiment_id: int | None = None
    hypothesis: str = ""


def format_review(payload: ReviewPayload) -> str:
    """Format a ReviewPayload into a markdown review comment."""
    icon = "✅" if payload.verdict == "KEEP" else "❌"
    lines = [
        f"## {icon} Factory Review: {payload.verdict}",
        "",
        f"**Verdict:** {payload.verdict}",
        f"**Reason:** {payload.reason}",
        "",
    ]

    if payload.experiment_id is not None:
        lines.append(f"**Experiment:** #{payload.experiment_id}")
    if payload.hypothesis:
        lines.append(f"**Hypothesis:** {payload.hypothesis}")
    lines.append("")

    # Score comparison
    lines.append("### Score Comparison")
    lines.append("")
    before = f"{payload.score_before:.4f}" if payload.score_before is not None else "n/a"
    after = f"{payload.score_after:.4f}" if payload.score_after is not None else "n/a"
    if payload.score_before is not None and payload.score_after is not None:
        delta = payload.score_after - payload.score_before
        delta_str = f"{delta:+.4f}"
    else:
        delta_str = "n/a"
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Before | {before} |")
    lines.append(f"| After | {after} |")
    lines.append(f"| Delta | {delta_str} |")
    lines.append(f"| Threshold | {payload.threshold:.4f} |")
    lines.append("")

    # Guard check table
    if payload.guard_results:
        lines.append("### Guard Checks")
        lines.append("")
        lines.append("| Check | Result |")
        lines.append("|-------|--------|")
        for check, result in payload.guard_results.items():
            icon_g = "✅" if result == "PASS" else "❌"
            lines.append(f"| {check} | {icon_g} {result} |")
        lines.append("")

    # Precheck summary
    if payload.precheck_summary:
        lines.append("### Precheck Gate")
        lines.append("")
        lines.append("```")
        lines.append(payload.precheck_summary)
        lines.append("```")
        lines.append("")

    # Code review notes
    if payload.code_notes:
        lines.append("### Code Review Notes")
        lines.append("")
        for note in payload.code_notes:
            lines.append(f"- {note}")
        lines.append("")

    lines.append("---")
    lines.append("*Posted by Factory CEO*")

    return "\n".join(lines)


def post_review(
    pr_number: int,
    review_body: str,
    verdict: str,
    repo: str | None = None,
    *,
    project_path: Path | None = None,
    forge: Forge | None = None,
) -> bool:
    """Post a review on a GitHub PR or GitLab MR.

    Uses ForgeOps to dispatch to the correct CLI. Falls back to GitHub
    when neither *project_path* nor *forge* is provided.
    Returns True on success.
    """
    log.info("post_review", pr=pr_number, verdict=verdict, repo=repo, forge=forge)

    if forge or project_path:
        resolved_forge: Forge = forge or "github"
        ops = ForgeOps(
            project_path or Path.cwd(),
            forge=resolved_forge,
            repo=repo or "",
        )
        return ops.post_review(pr_number, review_body, verdict)

    # Legacy path: no forge info, default to gh CLI directly
    if verdict == "KEEP":
        review_flag = "--approve"
    else:
        review_flag = "--request-changes"

    cmd = ["gh", "pr", "review", str(pr_number), review_flag, "--body", review_body]
    if repo:
        cmd.extend(["--repo", repo])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        log.error("post_review_timeout", pr=pr_number)
        return False
    except FileNotFoundError:
        log.error("post_review_gh_not_found")
        return False

    if result.returncode != 0:
        log.error(
            "post_review_failed",
            pr=pr_number,
            stderr=result.stderr[:200],
            returncode=result.returncode,
        )
        return False

    log.info("post_review_success", pr=pr_number)
    return True
