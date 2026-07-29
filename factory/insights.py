"""Cross-project insights — analyze experiment histories across all factory-managed projects."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import structlog

from factory.models import (
    CrossProjectInsights,
    ExperimentRecord,
    HypothesisOutcome,
    Pattern,
    ProjectSummary,
)

log = structlog.get_logger()

# ── hypothesis classification ────────────────────────────────────

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "bugfix": [
        "fix", "bug", "crash", "broken", "regression", "error handling",
        "race condition", "deadlock",
    ],
    "observability": [
        "log", "logging", "tracing", "telemetry", "structlog", "instrument",
        "observability", "monitor",
    ],
    "coverage": ["coverage", "test coverage", "uncovered", "untested"],
    "testing": ["test", "tests", "pytest", "assertion", "spec"],
    "lint": ["lint", "ruff", "flake8", "formatting", "style"],
    "type_safety": ["mypy", "type check", "type error", "type annotation", "typing"],
    "refactoring": ["refactor", "simplify", "restructure", "clean up", "rename"],
    "performance": [
        "performance", "optimize", "speed", "latency", "cache", "fast",
    ],
    "eval_improvement": [
        "eval", "score", "dimension", "threshold", "metric", "scoring",
    ],
    "agent_improvement": ["agent", "subagent", "invoke_agent", "spawn"],
    "prompt_engineering": ["prompt", "instruction", "persona", "skill.md"],
    "infrastructure": [
        "tmux", "cron", "deploy", "ci", "schedule", "heartbeat", "loop",
    ],
    "feature": [
        "add", "new", "implement", "page", "route", "endpoint", "command",
    ],
}

# Order matters: more specific categories first, "feature" last as default-ish
_CATEGORY_PRIORITY = [
    "bugfix", "observability", "coverage", "testing", "lint", "type_safety",
    "refactoring", "performance", "eval_improvement", "agent_improvement",
    "prompt_engineering", "infrastructure", "feature",
]


def classify_hypothesis(text: str) -> str:
    """Classify a hypothesis into one of 13 categories using keyword matching."""
    lower = text.lower()
    for category in _CATEGORY_PRIORITY:
        keywords = _CATEGORY_KEYWORDS[category]
        for kw in keywords:
            if kw in lower:
                return category
    return "feature"


# ── project discovery ────────────────────────────────────────────


def discover_projects(projects_dir: Path) -> list[Path]:
    """Find all factory-managed projects by scanning for .factory/results.tsv."""
    if not projects_dir.exists():
        log.debug("discover_projects_skip", reason="dir_not_found", path=str(projects_dir))
        return []
    projects: list[Path] = []
    for child in sorted(projects_dir.iterdir()):
        if not child.is_dir():
            continue
        tsv = child / ".factory" / "results.tsv"
        if tsv.exists():
            projects.append(child)
    log.info("discover_projects_complete", count=len(projects), dir=str(projects_dir))
    return projects


# ── history loading ──────────────────────────────────────────────


def load_all_histories(
    project_paths: list[Path],
) -> dict[str, list[ExperimentRecord]]:
    """Load experiment histories from each project's .factory/results.tsv."""
    from factory.store import ExperimentStore

    histories: dict[str, list[ExperimentRecord]] = {}
    for path in project_paths:
        store = ExperimentStore(path)
        records = asyncio.run(store.load_history())
        if records:
            histories[path.name] = records
            log.debug("load_history_complete", project=path.name, record_count=len(records))
    log.info("load_all_histories_complete", project_count=len(histories))
    return histories


# ── analysis ─────────────────────────────────────────────────────


def analyze(
    histories: dict[str, list[ExperimentRecord]],
) -> CrossProjectInsights:
    """Analyze experiment histories across projects and extract patterns."""
    projects: list[ProjectSummary] = []
    outcomes: list[HypothesisOutcome] = []

    for name, records in histories.items():
        kept = sum(1 for r in records if r.verdict == "keep")
        reverted = sum(1 for r in records if r.verdict == "revert")
        errored = sum(1 for r in records if r.verdict == "error")
        total = len(records)
        scores = [r.score_after for r in records if r.score_after is not None]

        projects.append(ProjectSummary(
            name=name,
            experiment_count=total,
            keep_count=kept,
            revert_count=reverted,
            error_count=errored,
            keep_rate=kept / total if total > 0 else 0.0,
            latest_score=scores[-1] if scores else None,
        ))

        for r in records:
            outcomes.append(HypothesisOutcome(
                hypothesis=r.hypothesis,
                verdict=r.verdict,
                category=classify_hypothesis(r.hypothesis),
                project=name,
                delta=r.delta,
            ))

    # Compute per-category stats
    category_stats: dict[str, dict[str, float]] = {}
    for outcome in outcomes:
        cat = outcome.category
        if cat not in category_stats:
            category_stats[cat] = {"total": 0, "kept": 0, "rate": 0.0}
        category_stats[cat]["total"] += 1
        if outcome.verdict == "keep":
            category_stats[cat]["kept"] += 1

    for stats in category_stats.values():
        if stats["total"] > 0:
            stats["rate"] = stats["kept"] / stats["total"]

    # Identify winning and losing categories (min 3 experiments for confidence)
    winning = [
        cat for cat, s in category_stats.items()
        if s["rate"] >= 0.8 and s["total"] >= 3
    ]
    losing = [
        cat for cat, s in category_stats.items()
        if s["rate"] < 0.5 and s["total"] >= 3
    ]

    # Extract patterns
    patterns = _extract_patterns(outcomes, category_stats)

    result = CrossProjectInsights(
        projects=projects,
        outcomes=outcomes,
        category_stats=category_stats,
        winning_categories=sorted(winning),
        losing_categories=sorted(losing),
        patterns=patterns,
        generated_at=datetime.now(),
    )
    log.info(
        "analyze_complete",
        project_count=len(projects),
        outcome_count=len(outcomes),
        winning=sorted(winning),
        losing=sorted(losing),
        pattern_count=len(patterns),
    )
    return result


def _extract_patterns(
    outcomes: list[HypothesisOutcome],
    category_stats: dict[str, dict[str, float]],
) -> list[Pattern]:
    """Extract recurring patterns from cross-project outcomes."""
    patterns: list[Pattern] = []

    # Pattern: categories that always succeed across multiple projects
    for cat, stats in category_stats.items():
        if stats["total"] < 3:
            continue
        cat_outcomes = [o for o in outcomes if o.category == cat]
        projects_seen = {o.project for o in cat_outcomes}

        if stats["rate"] >= 0.9 and len(projects_seen) >= 2:
            evidence = [
                f"{o.project} #{o.hypothesis[:40]}" for o in cat_outcomes if o.verdict == "keep"
            ][:5]
            patterns.append(Pattern(
                name=f"{cat}_reliable",
                description=(
                    f"{cat} experiments have {stats['rate']:.0%} keep rate "
                    f"across {len(projects_seen)} projects ({int(stats['total'])} total)"
                ),
                evidence=evidence,
                confidence=min(stats["rate"], len(projects_seen) / 3),
            ))

        if stats["rate"] < 0.5 and len(projects_seen) >= 2:
            evidence = [
                f"{o.project} #{o.hypothesis[:40]}" for o in cat_outcomes
                if o.verdict in ("revert", "error")
            ][:5]
            patterns.append(Pattern(
                name=f"{cat}_risky",
                description=(
                    f"{cat} experiments have only {stats['rate']:.0%} keep rate "
                    f"across {len(projects_seen)} projects — approach with caution"
                ),
                evidence=evidence,
                confidence=min(1.0 - stats["rate"], len(projects_seen) / 3),
            ))

    # Pattern: score trajectory — do projects plateau?
    for o in outcomes:
        if o.delta is not None and o.delta < -0.01 and o.verdict == "keep":
            patterns.append(Pattern(
                name="kept_with_regression",
                description=(
                    f"Experiment kept despite score regression "
                    f"(delta={o.delta:+.3f}) in {o.project}"
                ),
                evidence=[f"{o.project}: {o.hypothesis[:50]}"],
                confidence=0.5,
            ))
            break  # Only flag once

    return patterns


# ── formatting ───────────────────────────────────────────────────


def format_insights(insights: CrossProjectInsights) -> str:
    """Format cross-project insights into a markdown report."""
    lines = [
        f"# Cross-Project Insights — {insights.generated_at.strftime('%Y-%m-%d')}",
        "",
    ]

    # Summary
    total_exp = sum(p.experiment_count for p in insights.projects)
    total_kept = sum(p.keep_count for p in insights.projects)
    overall_rate = total_kept / total_exp if total_exp > 0 else 0.0

    lines.append(
        f"**{len(insights.projects)} projects**, "
        f"**{total_exp} experiments**, "
        f"**{overall_rate:.0%} overall keep rate**"
    )
    lines.append("")

    # Per-project summary
    lines.append("## Projects")
    lines.append("")
    for p in insights.projects:
        score = f", score: {p.latest_score:.3f}" if p.latest_score is not None else ""
        lines.append(
            f"- **{p.name}**: {p.experiment_count} experiments, "
            f"{p.keep_rate:.0%} keep rate{score}"
        )
    lines.append("")

    # Category success rates
    lines.append("## Category Success Rates")
    lines.append("")
    lines.append("| Category | Total | Kept | Rate |")
    lines.append("|----------|-------|------|------|")
    for cat in _CATEGORY_PRIORITY:
        if cat not in insights.category_stats:
            continue
        s = insights.category_stats[cat]
        lines.append(
            f"| {cat} | {int(s['total'])} | {int(s['kept'])} | {s['rate']:.0%} |"
        )
    lines.append("")

    # Winning strategies
    if insights.winning_categories:
        lines.append("## Winning Strategies (>80% keep, 3+ experiments)")
        lines.append("")
        for cat in insights.winning_categories:
            s = insights.category_stats[cat]
            lines.append(
                f"- **{cat}**: {s['rate']:.0%} keep rate ({int(s['total'])} experiments)"
            )
        lines.append("")

    # Losing strategies
    if insights.losing_categories:
        lines.append("## Risky Categories (<50% keep, 3+ experiments)")
        lines.append("")
        for cat in insights.losing_categories:
            s = insights.category_stats[cat]
            lines.append(
                f"- **{cat}**: {s['rate']:.0%} keep rate ({int(s['total'])} experiments)"
            )
        lines.append("")

    # Patterns
    if insights.patterns:
        lines.append("## Patterns")
        lines.append("")
        for pat in insights.patterns:
            lines.append(f"### {pat.name}")
            lines.append(f"_{pat.description}_")
            lines.append(f"Confidence: {pat.confidence:.1f}")
            lines.append("")
            for e in pat.evidence:
                lines.append(f"- {e}")
            lines.append("")

    return "\n".join(lines)
