"""Obsidian note creation — experiment logs, project dashboards, strategy notes."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from factory.models import CompositeScore, ExperimentRecord

_DEFAULT_VAULT = Path.home() / "Library" / "Mobile Documents" / "personal-vault"
_FACTORY_DIR = "Work/Factory"


def _get_vault_path() -> Path:
    """Get the Obsidian vault path from env var or default."""
    return Path(os.environ.get("OBSIDIAN_VAULT_PATH", str(_DEFAULT_VAULT)))


def _ensure_dir(path: Path) -> None:
    """Create directory and parents if needed."""
    path.mkdir(parents=True, exist_ok=True)


def write_experiment_note(
    project_name: str,
    record: ExperimentRecord,
    score_before: CompositeScore | None = None,
    score_after: CompositeScore | None = None,
) -> Path:
    """Create an Obsidian note for a completed experiment."""
    vault = _get_vault_path()
    experiments_dir = vault / _FACTORY_DIR / "Experiments"
    _ensure_dir(experiments_dir)

    filename = f"{project_name}-{record.id:03d}.md"
    note_path = experiments_dir / filename

    delta_str = f"{record.delta:+.4f}" if record.delta is not None else "n/a"
    before_str = f"{record.score_before:.4f}" if record.score_before is not None else "n/a"
    after_str = f"{record.score_after:.4f}" if record.score_after is not None else "n/a"
    date_str = record.timestamp.strftime("%Y-%m-%d")

    lines = [
        "---",
        "tags:",
        "  - factory",
        "  - experiment",
        f"  - {project_name}",
        f"project: {project_name}",
        f"experiment_id: {record.id}",
        f"verdict: {record.verdict}",
        f"score_delta: {record.delta if record.delta is not None else 0.0}",
        f"date: {date_str}",
        "---",
        "",
        f"# Experiment #{record.id}: {record.hypothesis[:80]}",
        "",
        "## Hypothesis",
        record.hypothesis,
        "",
        "## Result",
        f"**{record.verdict.upper()}** — score changed from {before_str} to {after_str} ({delta_str})",
        "",
        "## What Changed",
        record.change_summary or "No summary provided.",
        "",
    ]

    # Add eval details table if scores available
    if score_before and score_after:
        lines.extend([
            "## Eval Details",
            "| Dimension | Before | After | Delta |",
            "|-----------|--------|-------|-------|",
        ])
        before_map = {r.name: r.score for r in score_before.results}
        for r in score_after.results:
            b = before_map.get(r.name, 0.0)
            d = r.score - b
            lines.append(f"| {r.name} | {b:.2f} | {r.score:.2f} | {d:+.2f} |")
        lines.append("")

    if record.notes:
        lines.extend(["## Notes", record.notes, ""])

    lines.extend([
        "## Links",
        f"- [[{project_name} Dashboard]]",
    ])
    if record.issue_number:
        lines.append(f"- Issue: #{record.issue_number}")
    if record.pr_number:
        lines.append(f"- PR: #{record.pr_number}")

    note_path.write_text("\n".join(lines) + "\n")
    return note_path


def write_project_dashboard(
    project_name: str,
    state: str,
    current_score: float | None,
    records: list[ExperimentRecord],
    eval_dimensions: list[dict] | None = None,
) -> Path:
    """Create or update the project dashboard note."""
    vault = _get_vault_path()
    projects_dir = vault / _FACTORY_DIR / "Projects"
    _ensure_dir(projects_dir)

    filename = f"{project_name}.md"
    note_path = projects_dir / filename

    kept = sum(1 for r in records if r.verdict == "keep")
    reverted = sum(1 for r in records if r.verdict == "revert")
    errored = sum(1 for r in records if r.verdict == "error")
    score_str = f"{current_score:.4f}" if current_score is not None else "n/a"

    lines = [
        "---",
        "tags:",
        "  - factory",
        "  - project",
        f"  - {project_name}",
        "---",
        "",
        f"# Factory: {project_name}",
        "",
        "## Status",
        f"- **State**: {state}",
        f"- **Current Score**: {score_str}",
        f"- **Experiments Run**: {len(records)}",
        f"- **Kept**: {kept}, **Reverted**: {reverted}, **Error**: {errored}",
        "",
    ]

    if eval_dimensions:
        lines.append("## Eval Dimensions")
        for dim in eval_dimensions:
            lines.append(f"- {dim.get('name', '?')} ({dim.get('weight', 0):.1%} weight) — {dim.get('description', '')}")
        lines.append("")

    # Recent experiments (last 5)
    lines.append("## Recent Experiments")
    recent = records[-5:] if records else []
    for r in reversed(recent):
        delta = f"{r.delta:+.4f}" if r.delta is not None else "n/a"
        lines.append(f"- [[{project_name}-{r.id:03d}]] — {r.hypothesis[:50]} ({r.verdict.upper()}, {delta})")
    if not recent:
        lines.append("- No experiments yet")
    lines.append("")

    note_path.write_text("\n".join(lines) + "\n")
    return note_path


def write_strategy_note(
    project_name: str,
    strategy_content: str,
) -> Path:
    """Write a strategy snapshot to Obsidian."""
    vault = _get_vault_path()
    strategies_dir = vault / _FACTORY_DIR / "Strategies"
    _ensure_dir(strategies_dir)

    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{project_name}-{date_str}.md"
    note_path = strategies_dir / filename

    lines = [
        "---",
        "tags:",
        "  - factory",
        "  - strategy",
        f"  - {project_name}",
        f"date: {date_str}",
        "---",
        "",
        f"# Strategy: {project_name} — {date_str}",
        "",
        strategy_content,
    ]

    note_path.write_text("\n".join(lines) + "\n")
    return note_path
