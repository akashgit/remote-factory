"""Session summary — generate an end-of-cycle report for a factory run."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import structlog

from factory.events import load_events
from factory.models import CompositeScore, SessionSummary
from factory.store import ExperimentStore
from factory.strategy import categorize_hypothesis

log = structlog.get_logger()

_MARGINAL_DELTA_THRESHOLD = 0.01


async def generate_summary(project_path: Path) -> SessionSummary:
    """Build a SessionSummary from the project's .factory/ state.

    Scopes to the current session by filtering experiments to those recorded
    after the most recent ``cycle.started`` event.  Falls back to all
    experiments when no cycle event exists (e.g. manual invocation).
    """
    store = ExperimentStore(project_path)
    all_records = await store.load_history()
    cycle_start = _latest_cycle_start(project_path)
    records = (
        [r for r in all_records if r.timestamp >= cycle_start]
        if cycle_start
        else all_records
    )

    kept = [r for r in records if r.verdict == "keep"]
    reverted = [r for r in records if r.verdict == "revert"]
    errored = [r for r in records if r.verdict == "error"]

    backlog = _read_backlog(project_path)
    violations = _collect_guard_violations(project_path, [r.id for r in records])

    needs_input: list[str] = []
    for r in errored:
        needs_input.append(f"Experiment #{r.id} [ERROR]: {r.hypothesis}")
    for v in violations:
        needs_input.append(f"Guard violation: {v}")
    for r in reverted:
        if r.delta is not None and abs(r.delta) < _MARGINAL_DELTA_THRESHOLD:
            needs_input.append(
                f"Experiment #{r.id} [MARGINAL REVERT, delta={r.delta:+.4f}]: {r.hypothesis}"
            )

    score_start: float | None = None
    score_end: float | None = None
    if records:
        score_start = records[0].score_before
        score_end = records[-1].score_after

    costs = [r.cost_usd for r in records if r.cost_usd is not None]
    total_cost = sum(costs) if costs else None

    mode = _detect_mode(project_path)

    return SessionSummary(
        project_name=project_path.resolve().name,
        generated_at=datetime.now(timezone.utc),
        mode=mode,
        experiments_kept=kept,
        experiments_reverted=reverted,
        experiments_errored=errored,
        backlog_remaining=backlog,
        guard_violations=violations,
        needs_human_input=needs_input,
        score_start=score_start,
        score_end=score_end,
        total_cost_usd=total_cost,
    )


def format_summary(summary: SessionSummary) -> str:
    """Render a SessionSummary as human-readable markdown."""
    total = (
        len(summary.experiments_kept)
        + len(summary.experiments_reverted)
        + len(summary.experiments_errored)
    )
    net_delta = _net_delta(summary)
    lines: list[str] = [
        f"# Session Summary — {summary.project_name}",
        "",
        f"_Generated: {summary.generated_at.strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "## Overview",
        "",
        f"- **Mode:** {summary.mode}",
        f"- **Experiments:** {total} total "
        f"({len(summary.experiments_kept)} kept, "
        f"{len(summary.experiments_reverted)} reverted, "
        f"{len(summary.experiments_errored)} errors)",
    ]

    if summary.score_start is not None and summary.score_end is not None:
        lines.append(
            f"- **Score:** {summary.score_start:.4f} → {summary.score_end:.4f}"
            f" (net: {net_delta:+.4f})"
        )
    if summary.total_cost_usd is not None:
        lines.append(f"- **Cost:** ${summary.total_cost_usd:.2f}")

    lines.append("")

    # What Was Built
    lines.append("## What Was Built")
    lines.append("")
    if summary.experiments_kept:
        lines.append("| # | Hypothesis | Category | Delta | PR |")
        lines.append("|---|------------|----------|-------|----|")
        for r in summary.experiments_kept:
            cat = categorize_hypothesis(r.hypothesis).name
            delta_str = f"{r.delta:+.4f}" if r.delta is not None else "—"
            pr_str = f"#{r.pr_number}" if r.pr_number else "—"
            lines.append(f"| {r.id} | {r.hypothesis[:60]} | {cat} | {delta_str} | {pr_str} |")
    else:
        lines.append("No experiments were kept this session.")
    lines.append("")

    # What Was Deferred
    lines.append("## What Was Deferred")
    lines.append("")
    if summary.backlog_remaining:
        for item in summary.backlog_remaining:
            lines.append(f"- {item}")
    else:
        lines.append("No items in backlog.")
    lines.append("")

    # Needs Your Input
    lines.append("## Needs Your Input")
    lines.append("")
    if summary.needs_human_input:
        for item in summary.needs_human_input:
            lines.append(f"- {item}")
    else:
        lines.append("Nothing requires your attention.")
    lines.append("")

    return "\n".join(lines)


async def save_summary(project_path: Path, summary: SessionSummary) -> Path:
    """Persist the summary as markdown and JSON under .factory/reviews/."""
    reviews_dir = project_path / ".factory" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    md_path = reviews_dir / "session-summary.md"
    md_path.write_text(format_summary(summary))

    json_path = reviews_dir / "session-summary.json"
    json_path.write_text(
        json.dumps(summary.model_dump(), indent=2, default=str) + "\n"
    )

    log.info("summary.saved", md=str(md_path), json=str(json_path))
    return md_path


def _read_backlog(project_path: Path) -> list[str]:
    """Read backlog items, delegating to study module's parser."""
    try:
        from factory.study import _parse_backlog_items
        return _parse_backlog_items(project_path)
    except (ImportError, OSError):
        log.debug("summary.backlog_read_failed")
        return []


def _collect_guard_violations(
    project_path: Path,
    experiment_ids: list[int],
) -> list[str]:
    """Collect unique guard violations from eval_after.json files."""
    violations: list[str] = []
    for exp_id in experiment_ids:
        eval_path = (
            project_path / ".factory" / "experiments"
            / f"{exp_id:03d}" / "eval_after.json"
        )
        if not eval_path.exists():
            continue
        try:
            data = json.loads(eval_path.read_text())
            score = CompositeScore(**data)
            for v in score.guard_violations:
                if v not in violations:
                    violations.append(v)
        except (json.JSONDecodeError, OSError, ValueError, KeyError):
            continue
    return violations


def _latest_cycle_start(project_path: Path) -> datetime | None:
    """Return the timestamp of the most recent ``cycle.started`` event, or None."""
    events = load_events(project_path)
    for ev in reversed(events):
        if ev.get("type") == "cycle.started":
            try:
                return datetime.fromisoformat(ev["timestamp"])
            except (ValueError, KeyError):
                return None
    return None


def _detect_mode(project_path: Path) -> str:
    """Best-effort mode detection from events or sprint.started log."""
    events = load_events(project_path)
    for ev in reversed(events):
        if ev.get("type") in ("cycle.started", "sprint.started") and "mode" in ev.get("data", {}):
            return ev["data"]["mode"]
    return "unknown"


def _net_delta(summary: SessionSummary) -> float:
    if summary.score_start is not None and summary.score_end is not None:
        return summary.score_end - summary.score_start
    return 0.0
