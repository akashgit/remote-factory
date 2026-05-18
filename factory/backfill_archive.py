"""Backfill archive notes for experiments missing from .factory/archive/experiments/."""

import json
from pathlib import Path

import structlog

from factory.store import ExperimentStore

log = structlog.get_logger()


def _read_artifact(exp_dir: Path, filename: str) -> str | None:
    """Read a text artifact from an experiment directory, returning None if missing."""
    path = exp_dir / filename
    if not path.exists():
        return None
    return path.read_text()


def _read_json_artifact(exp_dir: Path, filename: str) -> dict | None:
    """Read a JSON artifact from an experiment directory, returning None if missing."""
    text = _read_artifact(exp_dir, filename)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _format_score_section(label: str, eval_data: dict | None) -> str:
    """Format an eval JSON blob into a readable markdown section."""
    if eval_data is None:
        return f"**{label}:** N/A\n"
    total = eval_data.get("total", "N/A")
    lines = [f"**{label}:** {total}\n"]
    for result in eval_data.get("results", []):
        name = result.get("name", "?")
        score = result.get("score", "?")
        passed = "PASS" if result.get("passed") else "FAIL"
        lines.append(f"- {name}: {score} ({passed})")
    return "\n".join(lines) + "\n"


def _generate_note(
    project_name: str,
    exp_id: int,
    exp_dir: Path,
    record: dict | None,
) -> str:
    """Generate a structured markdown archive note for an experiment."""
    hypothesis = _read_artifact(exp_dir, "hypothesis.md") or "N/A"
    eval_before = _read_json_artifact(exp_dir, "eval_before.json")
    eval_after = _read_json_artifact(exp_dir, "eval_after.json")
    verdict_data = _read_json_artifact(exp_dir, "verdict.json")
    diff_text = _read_artifact(exp_dir, "changes.diff")

    verdict = "N/A"
    change_summary = ""
    timestamp = ""
    delta = None
    notes = ""

    if verdict_data:
        verdict = verdict_data.get("verdict", "N/A")
        change_summary = verdict_data.get("change_summary", "")
        timestamp = verdict_data.get("timestamp", "")
        delta = verdict_data.get("delta")
        notes = verdict_data.get("notes", "")
    elif record:
        verdict = record.get("verdict", "N/A")
        change_summary = record.get("change_summary", "")
        timestamp = str(record.get("timestamp", ""))
        delta = record.get("delta")
        notes = record.get("notes", "")

    delta_str = f"{delta:+.4f}" if delta is not None else "N/A"

    sections = [
        f"# Experiment {exp_id:03d} — {project_name}\n",
    ]

    if timestamp:
        sections.append(f"**Date:** {timestamp}\n")

    sections.append(f"**Verdict:** {verdict}\n")
    sections.append(f"**Delta:** {delta_str}\n")

    sections.append(f"\n## Hypothesis\n\n{hypothesis.strip()}\n")

    if change_summary:
        sections.append(f"\n## What Changed\n\n{change_summary}\n")

    sections.append(f"\n## Eval Delta\n\n{_format_score_section('Before', eval_before)}")
    sections.append(_format_score_section("After", eval_after))

    sections.append(f"\n## Decision Rationale\n\n**Verdict:** {verdict}")
    if notes:
        sections.append(f"\n{notes}")

    if diff_text and diff_text.strip():
        truncated = diff_text[:5000]
        if len(diff_text) > 5000:
            truncated += "\n... (truncated)"
        sections.append(f"\n## Changes (diff)\n\n```diff\n{truncated}\n```\n")

    return "\n".join(sections) + "\n"


async def backfill_archive(project_path: Path) -> dict[str, int]:
    """Scan experiments and generate archive notes for those missing from archive.

    Returns a dict with keys: existed, created, total.
    """
    store = ExperimentStore(project_path)
    factory_dir = project_path / ".factory"
    experiments_dir = factory_dir / "experiments"
    archive_dir = factory_dir / "archive" / "experiments"

    if not experiments_dir.exists():
        log.info("backfill_archive_no_experiments", path=str(experiments_dir))
        return {"existed": 0, "created": 0, "total": 0}

    archive_dir.mkdir(parents=True, exist_ok=True)

    project_name = project_path.resolve().name

    records = await store.load_history()
    records_by_id: dict[int, dict] = {}
    for r in records:
        records_by_id[r.id] = r.model_dump()

    exp_dirs = sorted(
        [d for d in experiments_dir.iterdir() if d.is_dir() and d.name.isdigit()],
        key=lambda d: int(d.name),
    )

    existed = 0
    created = 0

    for exp_dir in exp_dirs:
        exp_id = int(exp_dir.name)
        note_filename = f"{project_name}-{exp_id:03d}.md"
        note_path = archive_dir / note_filename

        if note_path.exists():
            existed += 1
            continue

        record = records_by_id.get(exp_id)
        note_content = _generate_note(project_name, exp_id, exp_dir, record)
        note_path.write_text(note_content)
        created += 1
        log.info("backfill_archive_created", exp_id=exp_id, path=str(note_path))

    total = existed + created
    log.info(
        "backfill_archive_complete",
        existed=existed,
        created=created,
        total=total,
    )
    return {"existed": existed, "created": created, "total": total}
