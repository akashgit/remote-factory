"""PR review formatting and posting — posts structured reviews on GitHub PRs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

import structlog

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
    qa_body: str = ""
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

    # Score comparison (skip when both scores are absent, e.g. standalone PR reviews)
    if payload.score_before is not None or payload.score_after is not None:
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

    if payload.qa_body:
        lines.append("### QA Analysis")
        lines.append("")
        lines.append(payload.qa_body)
        lines.append("")

    lines.append("---")
    lines.append("*Posted by Factory CEO*")

    return "\n".join(lines)


def _post_comment(
    pr_number: int,
    body: str,
    repo: str | None = None,
) -> bool:
    """Post a plain comment on a PR using gh CLI."""
    cmd = ["gh", "pr", "comment", str(pr_number), "--body", body]
    if repo:
        cmd.extend(["--repo", repo])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return result.returncode == 0


def post_review(
    pr_number: int,
    review_body: str,
    verdict: str,
    repo: str | None = None,
) -> bool:
    """Post a review on a GitHub PR using gh CLI.

    Tries ``gh pr review`` first (formal approval/request-changes). If that
    fails (common in CI where GITHUB_TOKEN lacks review permissions), falls
    back to posting a plain PR comment so the review is still visible.
    """
    if verdict == "KEEP":
        review_flag = "--approve"
    else:
        review_flag = "--request-changes"

    cmd = ["gh", "pr", "review", str(pr_number), review_flag, "--body", review_body]
    if repo:
        cmd.extend(["--repo", repo])

    log.info("post_review", pr=pr_number, verdict=verdict, repo=repo)

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

    if result.returncode == 0:
        log.info("post_review_success", pr=pr_number)
        return True

    log.warning(
        "post_review_fallback_to_comment",
        pr=pr_number,
        stderr=result.stderr[:200],
    )
    if _post_comment(pr_number, review_body, repo=repo):
        log.info("post_review_comment_success", pr=pr_number)
        return True

    log.error("post_review_comment_failed", pr=pr_number)
    return False
