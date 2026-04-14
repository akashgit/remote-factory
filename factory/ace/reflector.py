"""Reflector — analyze experiment outcomes and generate candidate playbook bullets.

Reads experiment histories across all managed projects and produces per-agent
candidate bullets based on statistical patterns. This is deterministic pattern
extraction (no LLM needed) — the data speaks for itself.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from factory.ace.models import PlaybookItem
from factory.insights import (
    classify_hypothesis,
    discover_projects,
    load_all_histories,
)
from factory.models import ExperimentRecord

log = structlog.get_logger()

# Role prefixes for playbook item IDs
_ROLE_PREFIX = {
    "strategist": "strat",
    "builder": "build",
    "evaluator": "eval",
    "researcher": "res",
    "reviewer": "rev",
    "archivist": "arch",
}


def _make_id(role: str, counter: int) -> str:
    prefix = _ROLE_PREFIX.get(role, role[:5])
    return f"{prefix}-{counter:05d}"


def _category_stats(
    outcomes: list[tuple[str, str, float | None]],
) -> dict[str, dict[str, int | float]]:
    """Compute per-category keep/revert stats from (category, verdict, delta) tuples."""
    stats: dict[str, dict[str, int | float]] = {}
    for cat, verdict, delta in outcomes:
        if cat not in stats:
            stats[cat] = {"total": 0, "kept": 0, "reverted": 0, "pos_delta": 0, "neg_delta": 0}
        stats[cat]["total"] += 1
        if verdict == "keep":
            stats[cat]["kept"] += 1
            if delta is not None and delta > 0:
                stats[cat]["pos_delta"] += 1
        elif verdict == "revert":
            stats[cat]["reverted"] += 1
            if delta is not None and delta < 0:
                stats[cat]["neg_delta"] += 1
    for s in stats.values():
        s["rate"] = s["kept"] / s["total"] if s["total"] > 0 else 0.0
    return stats


def _detect_repetition(records: list[ExperimentRecord], window: int = 5) -> list[str]:
    """Detect categories that dominate the last N experiments."""
    if len(records) < window:
        return []
    recent = records[-window:]
    cats = [classify_hypothesis(r.hypothesis) for r in recent]
    from collections import Counter
    counts = Counter(cats)
    return [cat for cat, count in counts.items() if count >= window - 1]


def _strategist_bullets(
    outcomes: list[tuple[str, str, float | None]],
    records: list[ExperimentRecord],
) -> list[PlaybookItem]:
    """Generate strategist playbook bullets from experiment patterns."""
    bullets: list[PlaybookItem] = []
    stats = _category_stats(outcomes)
    counter = 1

    # High-keep categories → DO bullets
    for cat, s in stats.items():
        if s["total"] >= 5 and s["rate"] >= 0.8:
            kept = int(s["kept"])
            total = int(s["total"])
            bullets.append(PlaybookItem(
                id=_make_id("strategist", counter),
                content=f"Prioritize {cat} hypotheses — {kept}/{total} kept ({s['rate']:.0%} success rate)",
                helpful=kept,
                harmful=int(s["reverted"]),
                section="DO",
            ))
            counter += 1

    # Low-keep categories → DON'T bullets
    for cat, s in stats.items():
        if s["total"] >= 5 and s["rate"] < 0.4:
            reverted = int(s["reverted"])
            total = int(s["total"])
            bullets.append(PlaybookItem(
                id=_make_id("strategist", counter),
                content=f"Avoid {cat} hypotheses — only {s['rate']:.0%} keep rate ({reverted}/{total} reverted)",
                helpful=int(s["kept"]),
                harmful=reverted,
                section="DON'T",
            ))
            counter += 1

    # Repetition detection → DON'T bullet
    repeated = _detect_repetition(records)
    for cat in repeated:
        bullets.append(PlaybookItem(
            id=_make_id("strategist", counter),
            content=f"Stop repeating {cat} experiments — category dominates recent history, explore other dimensions",
            helpful=0,
            harmful=len([r for r in records[-5:] if classify_hypothesis(r.hypothesis) == cat]),
            section="DON'T",
        ))
        counter += 1

    # Research-backed experiments perform better → DO bullet
    research_kws = ["research", "paper", "study", "literature", "survey", "arxiv", "github.com"]
    # Check via hypothesis text (outcomes don't carry full text)
    research_records = [r for r in records if any(kw in r.hypothesis.lower() for kw in research_kws)]
    if len(research_records) >= 3:
        research_keep_rate = sum(1 for r in research_records if r.verdict == "keep") / len(research_records)
        all_keep_rate = sum(1 for _, v, _ in outcomes if v == "keep") / len(outcomes) if outcomes else 0
        if research_keep_rate > all_keep_rate + 0.1:
            bullets.append(PlaybookItem(
                id=_make_id("strategist", counter),
                content="Ground hypotheses in research (papers, similar projects) — research-backed experiments have higher keep rates",
                helpful=sum(1 for r in research_records if r.verdict == "keep"),
                harmful=sum(1 for r in research_records if r.verdict != "keep"),
                section="DO",
            ))
            counter += 1

    # Positive delta categories → DO bullet
    for cat, s in stats.items():
        if s["total"] >= 3 and s["pos_delta"] >= 2:
            bullets.append(PlaybookItem(
                id=_make_id("strategist", counter),
                content=f"Build on {cat} momentum — {int(s['pos_delta'])}/{int(s['total'])} experiments produced positive score deltas",
                helpful=int(s["pos_delta"]),
                harmful=int(s["neg_delta"]),
                section="DO",
            ))
            counter += 1

    return bullets


def _builder_bullets(
    outcomes: list[tuple[str, str, float | None]],
    records: list[ExperimentRecord],
) -> list[PlaybookItem]:
    """Generate builder playbook bullets from implementation patterns."""
    bullets: list[PlaybookItem] = []
    counter = 1

    # Check if small-scope changes succeed more than large ones
    if len(records) >= 5:
        short_summary = [r for r in records if r.change_summary and len(r.change_summary) < 100]
        long_summary = [r for r in records if r.change_summary and len(r.change_summary) >= 100]
        if len(short_summary) >= 3 and len(long_summary) >= 3:
            short_keep = sum(1 for r in short_summary if r.verdict == "keep") / len(short_summary)
            long_keep = sum(1 for r in long_summary if r.verdict == "keep") / len(long_summary)
            if short_keep > long_keep + 0.15:
                bullets.append(PlaybookItem(
                    id=_make_id("builder", counter),
                    content="Keep changes small and focused — shorter change summaries correlate with higher keep rates",
                    helpful=sum(1 for r in short_summary if r.verdict == "keep"),
                    harmful=sum(1 for r in short_summary if r.verdict != "keep"),
                    section="DO",
                ))
                counter += 1

    return bullets


def _evaluator_bullets(
    outcomes: list[tuple[str, str, float | None]],
    records: list[ExperimentRecord],
) -> list[PlaybookItem]:
    """Generate evaluator playbook bullets from scoring patterns."""
    bullets: list[PlaybookItem] = []
    counter = 1

    # Detect kept-with-regression (eval may be misleading)
    misleading = [
        r for r in records
        if r.verdict == "keep" and r.delta is not None and r.delta < -0.01
    ]
    if len(misleading) >= 2:
        bullets.append(PlaybookItem(
            id=_make_id("evaluator", counter),
            content=f"Flag score regressions even on kept experiments — {len(misleading)} experiments were kept despite negative deltas, eval may be misleading",
            helpful=0,
            harmful=len(misleading),
            section="DO",
        ))
        counter += 1

    return bullets


def reflect_on_experiments(
    projects_dir: Path,
    project_path: Path | None = None,
) -> dict[str, list[PlaybookItem]]:
    """Analyze experiment data across all managed projects and generate candidate bullets.

    Args:
        projects_dir: Directory containing factory-managed projects.
        project_path: Optional single project to also include (if not in projects_dir).

    Returns:
        Dict mapping agent role names to lists of candidate PlaybookItems.
    """
    # Discover and load all project histories
    project_paths = discover_projects(projects_dir)
    if project_path and project_path not in project_paths:
        project_paths.append(project_path)

    histories = load_all_histories(project_paths)
    if not histories:
        log.info("reflector_skip", reason="no_experiment_data")
        return {}

    # Flatten all records and build outcome tuples
    all_records: list[ExperimentRecord] = []
    outcomes: list[tuple[str, str, float | None]] = []
    for records in histories.values():
        all_records.extend(records)
        for r in records:
            cat = classify_hypothesis(r.hypothesis)
            outcomes.append((cat, r.verdict, r.delta))

    if not outcomes:
        return {}

    log.info("reflector_start", total_experiments=len(outcomes), projects=len(histories))

    # Generate per-role candidates
    candidates: dict[str, list[PlaybookItem]] = {
        "strategist": _strategist_bullets(outcomes, all_records),
        "builder": _builder_bullets(outcomes, all_records),
        "evaluator": _evaluator_bullets(outcomes, all_records),
    }

    # Filter empty roles
    candidates = {role: items for role, items in candidates.items() if items}

    log.info(
        "reflector_complete",
        roles=list(candidates.keys()),
        total_bullets=sum(len(v) for v in candidates.values()),
    )
    return candidates
