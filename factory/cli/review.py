"""CLI review commands."""
from __future__ import annotations

import argparse
import structlog
import sys
from pathlib import Path

from factory.cli._helpers import _emit_cli_event, _run

log = structlog.get_logger()

def cmd_refine_status(args: argparse.Namespace) -> int:
    """Print refinement state and regrounding output."""
    from factory.refine_state import format_status, read_state

    project_path = Path(args.path).resolve()
    state = read_state(project_path)
    print(format_status(state))
    return 0


def cmd_refine_begin(args: argparse.Namespace) -> int:
    """Record a new refinement entry and emit regrounding output."""
    from factory.refine_state import begin_refinement, format_begin

    project_path = Path(args.path).resolve()
    request = (args.request or "").strip()
    if not request:
        print("Error: --request must not be empty.", file=sys.stderr)
        return 1
    entry = begin_refinement(project_path, request)
    _emit_cli_event(project_path, "refine.begin", {
        "sequence": entry.sequence,
        "request": request[:200],
    })
    print(format_begin(entry))
    return 0


def cmd_refine_complete(args: argparse.Namespace) -> int:
    """Update the last refinement entry with a verdict."""
    from factory.refine_state import complete_refinement, read_state

    project_path = Path(args.path).resolve()
    verdict = args.verdict
    state = read_state(project_path)
    if not state.entries:
        print("Warning: no refinement entries found — nothing to complete.", file=sys.stderr)
        return 1
    last = state.entries[-1]
    mutated = complete_refinement(project_path, verdict)
    if not mutated:
        print(f"Warning: refinement #{last.sequence} is already completed.", file=sys.stderr)
        return 1
    _emit_cli_event(project_path, "refine.complete", {
        "sequence": last.sequence,
        "verdict": verdict,
    })
    print(f"Refinement #{last.sequence} completed — verdict: {verdict}")
    return 0


def cmd_clean_pr(args: argparse.Namespace) -> int:
    """Strip non-essential artifacts from a PR diff."""
    from factory.clean_pr import strip_pr_artifacts
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    config = _run(store.read_config())

    base_branch = config.target_branch or "main"
    exp_id = getattr(args, "exp", None)

    include = config.clean_pr_include or None
    exclude = config.clean_pr_exclude or None

    keep, stripped = strip_pr_artifacts(
        project_path,
        include=include,
        exclude=exclude,
        base_branch=base_branch,
        exp_id=exp_id,
    )

    if not stripped:
        print("Nothing to strip — all files are essential.")
        return 0

    print(f"Kept {len(keep)} files, stripped {len(stripped)} files:")
    for f in stripped:
        print(f"  - {f}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """Format and optionally post a review on a GitHub PR."""
    from factory.review import ReviewPayload, format_review, post_review

    guard_results: dict[str, str] = {}
    if args.guards:
        for pair in args.guards.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                guard_results[k.strip()] = v.strip()

    qa_body = ""
    if args.qa_body_file:
        body_path = Path(args.qa_body_file)
        if body_path.exists():
            qa_body = body_path.read_text().strip()

    payload = ReviewPayload(
        verdict=args.verdict.upper(),
        reason=args.reason or "",
        score_before=args.score_before,
        score_after=args.score_after,
        threshold=args.threshold,
        guard_results=guard_results,
        precheck_summary=args.precheck_summary or "",
        code_notes=[n.strip() for n in args.code_notes.split("|")] if args.code_notes else [],
        qa_body=qa_body,
        experiment_id=args.experiment_id,
        hypothesis=args.hypothesis or "",
    )

    review_body = format_review(payload)

    if args.pr and not args.dry_run:
        success = post_review(args.pr, review_body, payload.verdict, repo=args.repo)
        if success:
            print(f"Review posted on PR #{args.pr}")
        else:
            print(f"Failed to post review on PR #{args.pr}", file=sys.stderr)
            print(review_body)
            return 1
    else:
        print(review_body)

    return 0

