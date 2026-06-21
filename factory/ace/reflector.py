"""Reflector — analyze experiment outcomes and generate candidate playbook bullets.

Reads experiment histories across all managed projects and produces per-agent
candidate bullets based on statistical patterns. This is deterministic pattern
extraction (no LLM needed) — the data speaks for itself.

Factory v2: generates bullets for all agent roles (researcher, strategist,
builder, qa, archivist, ceo) by parsing structured CEO notes
from the experiment record notes field.

Counter wiring: after generating candidates, the Reflector also loads the
current playbook for each role and increments helpful/harmful counters on
existing bullets based on fuzzy-matching experiment hypothesis text against
bullet content. Kept experiments increment `helpful`, reverted experiments
increment `harmful`.
"""

from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import structlog

from factory.ace.models import Playbook, PlaybookItem
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
    "qa": "qa",
    "researcher": "res",
    "archivist": "arch",
    "ceo": "ceo",
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
    cat_counts = Counter(cats)
    return [cat for cat, count in cat_counts.items() if count >= window - 1]


def _parse_ceo_notes(notes: str) -> dict[str, str]:
    """Parse structured CEO metadata from experiment notes field.

    Expected format: 'ceo:keep score_delta=+0.05 agents_spawned=R,S,B,R,E builder_failed=false'
    Returns a dict of key=value pairs plus a 'decision' key for the ceo:keep/revert/error prefix.
    """
    parsed: dict[str, str] = {}
    if not notes:
        return parsed

    # Extract ceo:<decision> prefix
    m = re.match(r"ceo:(\w+)", notes)
    if m:
        parsed["decision"] = m.group(1)

    # Extract key=value pairs
    for kv_match in re.finditer(r"(\w+)=(\S+)", notes):
        parsed[kv_match.group(1)] = kv_match.group(2)

    return parsed


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


def _qa_health_bullets(
    outcomes: list[tuple[str, str, float | None]],
    records: list[ExperimentRecord],
) -> list[PlaybookItem]:
    """Generate QA health-check playbook bullets from scoring patterns."""
    bullets: list[PlaybookItem] = []
    counter = 1

    # Detect kept-with-regression (eval may be misleading)
    misleading = [
        r for r in records
        if r.verdict == "keep" and r.delta is not None and r.delta < -0.01
    ]
    if len(misleading) >= 2:
        bullets.append(PlaybookItem(
            id=_make_id("qa", counter),
            content=f"Flag score regressions even on kept experiments — {len(misleading)} experiments were kept despite negative deltas, eval may be misleading",
            helpful=0,
            harmful=len(misleading),
            section="DO",
        ))
        counter += 1

    return bullets


def _researcher_bullets(
    outcomes: list[tuple[str, str, float | None]],
    records: list[ExperimentRecord],
) -> list[PlaybookItem]:
    """Generate researcher playbook bullets from research impact patterns."""
    bullets: list[PlaybookItem] = []
    counter = 1

    # Check if experiments with research context succeed more often
    research_kws = ["research", "paper", "study", "literature", "survey", "arxiv", "github.com",
                    "best practice", "pattern", "reference"]
    research_records = [r for r in records if any(kw in r.hypothesis.lower() for kw in research_kws)]
    non_research = [r for r in records if r not in research_records]

    if len(research_records) >= 3 and len(non_research) >= 3:
        research_keep = sum(1 for r in research_records if r.verdict == "keep") / len(research_records)
        non_keep = sum(1 for r in non_research if r.verdict == "keep") / len(non_research)

        if research_keep > non_keep + 0.15:
            bullets.append(PlaybookItem(
                id=_make_id("researcher", counter),
                content=f"Deep research pays off — research-grounded experiments have {research_keep:.0%} keep rate vs {non_keep:.0%} without research",
                helpful=sum(1 for r in research_records if r.verdict == "keep"),
                harmful=sum(1 for r in research_records if r.verdict != "keep"),
                section="DO",
            ))
            counter += 1
        elif research_keep < non_keep - 0.1:
            bullets.append(PlaybookItem(
                id=_make_id("researcher", counter),
                content=f"Research is not translating to success — research-backed experiments keep at {research_keep:.0%} vs {non_keep:.0%} baseline. Focus on more actionable research",
                helpful=sum(1 for r in research_records if r.verdict == "keep"),
                harmful=sum(1 for r in research_records if r.verdict != "keep"),
                section="DON'T",
            ))
            counter += 1

    # Check if experiments with large positive deltas have common hypothesis patterns
    big_wins = [r for r in records if r.delta is not None and r.delta > 0.05 and r.verdict == "keep"]
    if len(big_wins) >= 3:
        win_cats = Counter(classify_hypothesis(r.hypothesis) for r in big_wins)
        top_cat, top_count = win_cats.most_common(1)[0]
        if top_count >= 2:
            bullets.append(PlaybookItem(
                id=_make_id("researcher", counter),
                content=f"Research {top_cat} deeply — {top_count}/{len(big_wins)} high-impact experiments (delta > +0.05) came from this category",
                helpful=top_count,
                harmful=0,
                section="DO",
            ))
            counter += 1

    return bullets


def _qa_review_bullets(
    outcomes: list[tuple[str, str, float | None]],
    records: list[ExperimentRecord],
    counter_offset: int = 0,
) -> list[PlaybookItem]:
    """Generate QA code-review playbook bullets from guard/review patterns."""
    bullets: list[PlaybookItem] = []
    counter = 1 + counter_offset

    # Parse CEO notes to find QA failures
    qa_failures = [r for r in records if "qa_failed=true" in (r.notes or "")]
    if len(qa_failures) >= 2:
        failure_cats = Counter(classify_hypothesis(r.hypothesis) for r in qa_failures)
        top_cat, top_count = failure_cats.most_common(1)[0]
        bullets.append(PlaybookItem(
            id=_make_id("qa", counter),
            content=f"Pay extra attention to {top_cat} changes — {top_count} guard violations in this category",
            helpful=0,
            harmful=top_count,
            section="DO",
        ))
        counter += 1

    # Detect false positives: experiments that were reverted despite positive delta
    # (suggests QA or CEO was too strict)
    strict_reverts = [
        r for r in records
        if r.verdict == "revert" and r.delta is not None and r.delta > 0.02
    ]
    if len(strict_reverts) >= 3:
        bullets.append(PlaybookItem(
            id=_make_id("qa", counter),
            content=f"Review strictness may be too high — {len(strict_reverts)} experiments reverted despite positive deltas (>+0.02). Check if guard rules are too conservative",
            helpful=0,
            harmful=len(strict_reverts),
            section="DON'T",
        ))
        counter += 1

    # Detect kept experiments with very small positive delta (near-zero improvement)
    marginal_keeps = [
        r for r in records
        if r.verdict == "keep" and r.delta is not None and 0 < r.delta < 0.005
    ]
    if len(marginal_keeps) >= 3:
        bullets.append(PlaybookItem(
            id=_make_id("qa", counter),
            content=f"Raise the bar on marginal improvements — {len(marginal_keeps)} experiments kept with delta < 0.005. These add complexity without meaningful gain",
            helpful=0,
            harmful=len(marginal_keeps),
            section="DO",
        ))
        counter += 1

    return bullets


def _archivist_bullets(
    outcomes: list[tuple[str, str, float | None]],
    records: list[ExperimentRecord],
) -> list[PlaybookItem]:
    """Generate archivist playbook bullets from archival compliance patterns."""
    bullets: list[PlaybookItem] = []
    counter = 1

    # Check archival compliance from CEO notes
    has_ceo_notes = [r for r in records if r.notes and r.notes.startswith("ceo:")]
    archived = [r for r in has_ceo_notes if "archivist_spawned=true" in (r.notes or "")]
    not_archived = [r for r in has_ceo_notes if "archivist_spawned=false" in (r.notes or "")]

    if len(not_archived) >= 2:
        bullets.append(PlaybookItem(
            id=_make_id("archivist", counter),
            content=f"Archival was skipped in {len(not_archived)} experiments — ensure every experiment outcome is recorded. Lost learnings cannot be recovered",
            helpful=0,
            harmful=len(not_archived),
            section="DON'T",
        ))
        counter += 1

    if len(archived) >= 5:
        bullets.append(PlaybookItem(
            id=_make_id("archivist", counter),
            content=f"Archival compliance is strong — {len(archived)} experiments properly recorded. Continue recording at all checkpoints",
            helpful=len(archived),
            harmful=0,
            section="DO",
        ))
        counter += 1

    # Check if experiments with notes have better outcomes (archival = learning)
    noted = [r for r in records if r.notes and len(r.notes) > 20]
    unnoted = [r for r in records if not r.notes or len(r.notes) <= 20]
    if len(noted) >= 5 and len(unnoted) >= 5:
        noted_keep = sum(1 for r in noted if r.verdict == "keep") / len(noted)
        unnoted_keep = sum(1 for r in unnoted if r.verdict == "keep") / len(unnoted)
        if noted_keep > unnoted_keep + 0.1:
            bullets.append(PlaybookItem(
                id=_make_id("archivist", counter),
                content=f"Detailed notes correlate with better outcomes — experiments with notes keep at {noted_keep:.0%} vs {unnoted_keep:.0%}. Write thorough archival notes",
                helpful=sum(1 for r in noted if r.verdict == "keep"),
                harmful=sum(1 for r in noted if r.verdict != "keep"),
                section="DO",
            ))
            counter += 1

    return bullets


def _ceo_bullets(
    outcomes: list[tuple[str, str, float | None]],
    records: list[ExperimentRecord],
) -> list[PlaybookItem]:
    """Generate CEO playbook bullets from orchestration and decision patterns."""
    bullets: list[PlaybookItem] = []
    counter = 1

    # Parse all CEO decision notes
    ceo_records = [(r, _parse_ceo_notes(r.notes or "")) for r in records if r.notes and r.notes.startswith("ceo:")]

    if not ceo_records:
        # No CEO-annotated records yet — generate bootstrapping bullets
        if len(records) >= 5:
            keep_rate = sum(1 for r in records if r.verdict == "keep") / len(records)
            if keep_rate < 0.3:
                bullets.append(PlaybookItem(
                    id=_make_id("ceo", counter),
                    content=f"Overall keep rate is only {keep_rate:.0%} — consider whether hypotheses are too ambitious or evals are too strict",
                    helpful=0,
                    harmful=sum(1 for r in records if r.verdict != "keep"),
                    section="DO",
                ))
                counter += 1
            elif keep_rate > 0.8:
                bullets.append(PlaybookItem(
                    id=_make_id("ceo", counter),
                    content=f"Keep rate is {keep_rate:.0%} — hypotheses may be too conservative. Push for more ambitious experiments",
                    helpful=sum(1 for r in records if r.verdict == "keep"),
                    harmful=0,
                    section="DO",
                ))
                counter += 1
        return bullets

    # Analyze CEO decision patterns
    keeps = [(r, n) for r, n in ceo_records if n.get("decision") == "keep"]
    reverts = [(r, n) for r, n in ceo_records if n.get("decision") == "revert"]
    errors = [(r, n) for r, n in ceo_records if n.get("decision") == "error"]

    # Builder failure rate
    builder_failures = [(r, n) for r, n in ceo_records if n.get("builder_failed") == "true"]
    if len(builder_failures) >= 3:
        failure_cats = Counter(classify_hypothesis(r.hypothesis) for r, _ in builder_failures)
        top_cat, top_count = failure_cats.most_common(1)[0]
        bullets.append(PlaybookItem(
            id=_make_id("ceo", counter),
            content=f"Builder fails often on {top_cat} tasks ({top_count} failures) — break these into smaller, more specific issues",
            helpful=0,
            harmful=top_count,
            section="DO",
        ))
        counter += 1

    # Keep accuracy: were keeps actually beneficial? (positive delta)
    if len(keeps) >= 3:
        good_keeps = sum(1 for r, _ in keeps if r.delta is not None and r.delta > 0)
        bad_keeps = sum(1 for r, _ in keeps if r.delta is not None and r.delta < 0)
        if bad_keeps >= 2:
            bullets.append(PlaybookItem(
                id=_make_id("ceo", counter),
                content=f"Tighten keep criteria — {bad_keeps}/{len(keeps)} kept experiments had negative deltas. You may be keeping marginal changes",
                helpful=good_keeps,
                harmful=bad_keeps,
                section="DON'T",
            ))
            counter += 1
        elif good_keeps == len(keeps):
            bullets.append(PlaybookItem(
                id=_make_id("ceo", counter),
                content=f"Keep decisions are accurate — all {good_keeps} keeps had positive deltas. Trust the eval scores",
                helpful=good_keeps,
                harmful=0,
                section="DO",
            ))
            counter += 1

    # Revert analysis: were reverts wise? (look for patterns in reverted categories)
    if len(reverts) >= 3:
        revert_cats = Counter(classify_hypothesis(r.hypothesis) for r, _ in reverts)
        chronic_reverts = [(cat, count) for cat, count in revert_cats.items() if count >= 3]
        for cat, count in chronic_reverts:
            bullets.append(PlaybookItem(
                id=_make_id("ceo", counter),
                content=f"Stop attempting {cat} experiments — {count} consecutive reverts. Ask Strategist to explore different dimensions",
                helpful=0,
                harmful=count,
                section="DON'T",
            ))
            counter += 1

    # Error rate analysis
    if errors and len(ceo_records) >= 5:
        error_rate = len(errors) / len(ceo_records)
        if error_rate > 0.2:
            bullets.append(PlaybookItem(
                id=_make_id("ceo", counter),
                content=f"High error rate ({error_rate:.0%}) — {len(errors)} experiments ended in error. Investigate root causes: builder crashes, eval failures, or timeout issues",
                helpful=0,
                harmful=len(errors),
                section="DO",
            ))
            counter += 1

    # Archival compliance tracking
    archived_count = sum(1 for _, n in ceo_records if n.get("archivist_spawned") == "true")
    skipped_count = sum(1 for _, n in ceo_records if n.get("archivist_spawned") == "false")
    if skipped_count >= 2:
        bullets.append(PlaybookItem(
            id=_make_id("ceo", counter),
            content=f"You skipped archival in {skipped_count} experiments — this violates Sacred Rule 7. Spawn the Archivist at EVERY checkpoint",
            helpful=archived_count,
            harmful=skipped_count,
            section="DON'T",
        ))
        counter += 1

    # Cost efficiency: track average cost per keep vs per revert
    keep_costs = [r.cost_usd for r, _ in keeps if r.cost_usd is not None]
    revert_costs = [r.cost_usd for r, _ in reverts if r.cost_usd is not None]
    if len(keep_costs) >= 3 and len(revert_costs) >= 3:
        avg_keep_cost = sum(keep_costs) / len(keep_costs)
        avg_revert_cost = sum(revert_costs) / len(revert_costs)
        if avg_revert_cost > avg_keep_cost * 1.5:
            bullets.append(PlaybookItem(
                id=_make_id("ceo", counter),
                content=f"Reverted experiments cost ${avg_revert_cost:.2f} avg vs ${avg_keep_cost:.2f} for keeps — wasted spend. Better hypothesis filtering could save ~${avg_revert_cost * len(reverts):.0f}",
                helpful=0,
                harmful=len(reverts),
                section="DO",
            ))
            counter += 1

    return bullets


# ── Counter wiring helpers ─────────────────────────────────────────


# Minimum similarity ratio to consider a bullet matching a hypothesis
_MATCH_THRESHOLD = 0.35


def _extract_key_terms(text: str) -> list[str]:
    """Extract meaningful terms from text for fuzzy matching."""
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "above", "below", "between", "and", "but", "or",
        "not", "no", "nor", "so", "yet", "both", "either", "neither", "each",
        "every", "all", "any", "few", "more", "most", "other", "some", "such",
        "than", "too", "very", "just", "also", "that", "this", "these", "those",
        "it", "its", "they", "them", "their", "we", "us", "our", "you", "your",
        "he", "she", "his", "her", "i", "me", "my",
    }
    words = re.findall(r"[a-z]+", text.lower())
    return [w for w in words if len(w) >= 3 and w not in stop_words]


def _hypothesis_matches_bullet(hypothesis: str, bullet_content: str) -> bool:
    """Check if key terms from hypothesis appear in bullet content (fuzzy).

    Uses two strategies:
    1. Key term overlap: if enough key terms from the hypothesis appear in the bullet
    2. SequenceMatcher similarity on the full text
    """
    hyp_terms = _extract_key_terms(hypothesis)
    bullet_terms = set(_extract_key_terms(bullet_content))

    if not hyp_terms:
        return False

    # Strategy 1: term overlap
    overlap = sum(1 for t in hyp_terms if t in bullet_terms)
    overlap_ratio = overlap / len(hyp_terms) if hyp_terms else 0.0
    if overlap_ratio >= 0.4:
        return True

    # Strategy 2: sequence similarity
    ratio = SequenceMatcher(None, hypothesis.lower(), bullet_content.lower()).ratio()
    return ratio >= _MATCH_THRESHOLD


def update_playbook_counters(
    playbook: Playbook,
    records: list[ExperimentRecord],
) -> Playbook:
    """Increment helpful/harmful counters on playbook bullets based on experiment outcomes.

    For each experiment record:
    - If verdict=keep, increment `helpful` on matching bullets
    - If verdict=revert, increment `harmful` on matching bullets

    Matching is fuzzy — checks if key terms from the hypothesis appear in bullet text.

    Args:
        playbook: The current playbook to update counters on.
        records: Experiment records to process.

    Returns:
        Updated Playbook with incremented counters.
    """
    if not playbook.items or not records:
        return playbook

    # Work with mutable copies
    updated_items = [
        PlaybookItem(
            id=item.id,
            content=item.content,
            helpful=item.helpful,
            harmful=item.harmful,
            section=item.section,
        )
        for item in playbook.items
    ]

    for record in records:
        if record.verdict not in ("keep", "revert"):
            continue

        for i, item in enumerate(updated_items):
            if _hypothesis_matches_bullet(record.hypothesis, item.content):
                if record.verdict == "keep":
                    updated_items[i] = PlaybookItem(
                        id=item.id,
                        content=item.content,
                        helpful=item.helpful + 1,
                        harmful=item.harmful,
                        section=item.section,
                    )
                elif record.verdict == "revert":
                    updated_items[i] = PlaybookItem(
                        id=item.id,
                        content=item.content,
                        helpful=item.helpful,
                        harmful=item.harmful + 1,
                        section=item.section,
                    )
                log.debug(
                    "counter_increment",
                    bullet_id=item.id,
                    verdict=record.verdict,
                    experiment_id=record.id,
                )

    return Playbook(role=playbook.role, updated=playbook.updated, items=updated_items)


def update_counters_from_experiments(
    playbooks_dir: Path,
    records: list[ExperimentRecord],
) -> dict[str, Playbook]:
    """Load all playbooks, update counters from experiment records, persist to disk.

    Args:
        playbooks_dir: Directory containing playbook .md files.
        records: Experiment records to process.

    Returns:
        Dict mapping role name to updated Playbook.
    """
    updated_playbooks: dict[str, Playbook] = {}

    for playbook_path in sorted(playbooks_dir.glob("*.md")):
        role = playbook_path.stem
        playbook = Playbook.from_markdown(playbook_path.read_text())
        updated = update_playbook_counters(playbook, records)

        # Only persist if counters actually changed
        if any(
            u.helpful != o.helpful or u.harmful != o.harmful
            for u, o in zip(updated.items, playbook.items)
        ):
            playbook_path.write_text(updated.to_markdown())
            log.info("counters_persisted", role=role, items=len(updated.items))

        updated_playbooks[role] = updated

    return updated_playbooks


def _load_from_reports(project_paths: list[Path]) -> tuple[
    list[ExperimentRecord],
    list[tuple[str, str, float | None]],
    dict[str, list[ExperimentRecord]],
]:
    """Load experiment data from performance reports with TSV fallback.

    Tries to read .factory/performance_report.json first. If not present,
    falls back to loading from TSV via load_all_histories().

    Returns:
        (all_records, outcomes, histories) tuple.
    """
    from factory.report import load_performance_report

    histories: dict[str, list[ExperimentRecord]] = {}
    report_loaded = False

    for path in project_paths:
        report = load_performance_report(path)
        if report and report.total_experiments > 0:
            from factory.store import ExperimentStore
            import asyncio
            try:
                records = asyncio.run(ExperimentStore(path).load_history())
                if records:
                    histories[path.resolve().name] = records
                    report_loaded = True
            except Exception:
                pass

    if not report_loaded:
        histories = load_all_histories(project_paths)

    all_records: list[ExperimentRecord] = []
    outcomes: list[tuple[str, str, float | None]] = []
    for records in histories.values():
        all_records.extend(records)
        for r in records:
            cat = classify_hypothesis(r.hypothesis)
            outcomes.append((cat, r.verdict, r.delta))

    return all_records, outcomes, histories


def _verdict_bullets(project_paths: list[Path]) -> list[PlaybookItem]:
    """Generate bullets from CEO verdict patterns across projects."""
    from factory.report import load_performance_report

    bullets: list[PlaybookItem] = []
    counter = 1

    all_patterns: dict[str, int] = {}
    for path in project_paths:
        report = load_performance_report(path)
        if not report:
            continue
        for key, count in report.verdict_patterns.items():
            all_patterns[key] = all_patterns.get(key, 0) + count

    redirect_total = sum(v for k, v in all_patterns.items() if ":REDIRECT" in k)
    abort_total = sum(v for k, v in all_patterns.items() if ":ABORT" in k)

    if redirect_total >= 3:
        top_redirects = sorted(
            [(k, v) for k, v in all_patterns.items() if ":REDIRECT" in k],
            key=lambda x: x[1], reverse=True,
        )
        role = top_redirects[0][0].split(":")[0]
        bullets.append(PlaybookItem(
            id=_make_id("ceo", 100 + counter),
            content=f"The {role} agent frequently needs REDIRECT ({top_redirects[0][1]} times) — consider improving its prompt or providing clearer task descriptions",
            helpful=0,
            harmful=redirect_total,
            section="DO",
        ))
        counter += 1

    if abort_total >= 2:
        bullets.append(PlaybookItem(
            id=_make_id("ceo", 100 + counter),
            content=f"Agent ABORTs occurred {abort_total} times across projects — investigate root causes (crashes, scope violations, or prompt issues)",
            helpful=0,
            harmful=abort_total,
            section="DO",
        ))
        counter += 1

    return bullets


def _observation_bullets(project_paths: list[Path]) -> list[PlaybookItem]:
    """Generate bullets from archivist observations across projects."""
    from factory.report import load_performance_report

    bullets: list[PlaybookItem] = []
    counter = 1

    obs_count = 0
    archive_count = 0
    for path in project_paths:
        report = load_performance_report(path)
        if not report:
            continue
        for obs in report.observations:
            if "archive" in obs.tags:
                archive_count += 1
            obs_count += 1

    if obs_count >= 10 and archive_count < obs_count * 0.3:
        bullets.append(PlaybookItem(
            id=_make_id("archivist", 100 + counter),
            content=f"Archive coverage is low — only {archive_count}/{obs_count} observations are from archive notes. Write more detailed experiment notes",
            helpful=archive_count,
            harmful=obs_count - archive_count,
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
    # Primary: scan projects_dir (backward compatible)
    project_paths = discover_projects(projects_dir)

    # Secondary: merge registry entries that aren't already discovered
    try:
        from factory.registry import get_project_paths
        for rp in get_project_paths():
            if rp.resolve() not in {p.resolve() for p in project_paths}:
                project_paths.append(rp)
    except Exception:
        pass

    if project_path and project_path not in project_paths:
        project_paths.append(project_path)

    # Load data from performance reports with TSV fallback
    all_records, outcomes, histories = _load_from_reports(project_paths)

    if not outcomes:
        log.info("reflector_skip", reason="no_experiment_data")
        return {}

    log.info("reflector_start", total_experiments=len(outcomes), projects=len(histories))

    # Generate per-role candidates
    candidates: dict[str, list[PlaybookItem]] = {
        "strategist": _strategist_bullets(outcomes, all_records),
        "builder": _builder_bullets(outcomes, all_records),
        "qa": (
            _qa_h := _qa_health_bullets(outcomes, all_records),
            _qa_h + _qa_review_bullets(outcomes, all_records, counter_offset=len(_qa_h)),
        )[-1],
        "researcher": _researcher_bullets(outcomes, all_records),
        "archivist": _archivist_bullets(outcomes, all_records),
        "ceo": _ceo_bullets(outcomes, all_records),
    }

    # Add verdict-pattern and observation bullets from performance reports
    verdict_items = _verdict_bullets(project_paths)
    if verdict_items:
        candidates.setdefault("ceo", []).extend(verdict_items)

    observation_items = _observation_bullets(project_paths)
    if observation_items:
        candidates.setdefault("archivist", []).extend(observation_items)

    # Filter empty roles
    candidates = {role: items for role, items in candidates.items() if items}

    log.info(
        "reflector_complete",
        roles=list(candidates.keys()),
        total_bullets=sum(len(v) for v in candidates.values()),
    )
    return candidates
