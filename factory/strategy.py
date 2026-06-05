"""FEEC priority heuristic and tiered experiment history.

Categorises and ranks hypotheses so the factory always addresses the
highest-leverage category first:
  1. FIX   — resolve a crash, error, or regression
  2. EXPLOIT — build on a recent success
  3. EXPLORE — try something new
  4. COMBINE — merge prior successful approaches

Also provides 3-tier context compression for experiment history:
  Tier 1 (last 3): full detail
  Tier 2 (4–10 back): one-line summaries
  Tier 3 (11+): aggregate stats only
"""

from __future__ import annotations

from collections import Counter
from enum import IntEnum
from typing import Any

import structlog

from factory.models import ExperimentRecord

log = structlog.get_logger()

MAX_INLINE_HISTORY = 10

# ── keywords per category (lowercase) ───────────────────────────────

_FIX_KEYWORDS: list[str] = [
    "fix", "error", "bug", "crash", "fail", "regression", "broken", "repair",
]
_EXPLOIT_KEYWORDS: list[str] = [
    "improve", "increase", "extend", "enhance", "build on", "optimize", "boost",
]
_COMBINE_KEYWORDS: list[str] = [
    "combine", "merge", "integrate", "unify", "consolidate",
]


class FEECCategory(IntEnum):
    """FEEC priority categories — lower value = higher priority."""

    FIX = 0
    EXPLOIT = 1
    EXPLORE = 2
    COMBINE = 3


def categorize_hypothesis(
    text: str,
    history: list[dict] | None = None,
) -> FEECCategory:
    """Classify *text* into a FEEC category using keyword matching.

    Checks FIX first, then EXPLOIT, then COMBINE.  If no keywords match the
    text is classified as EXPLORE (the default / catch-all category).

    ``history`` is accepted for forward-compatibility but not used by the
    keyword heuristic today.
    """
    lower = text.lower()

    if any(kw in lower for kw in _FIX_KEYWORDS):
        log.debug("categorize_hypothesis", category="FIX", text=text[:80])
        return FEECCategory.FIX

    if any(kw in lower for kw in _EXPLOIT_KEYWORDS):
        log.debug("categorize_hypothesis", category="EXPLOIT", text=text[:80])
        return FEECCategory.EXPLOIT

    if any(kw in lower for kw in _COMBINE_KEYWORDS):
        log.debug("categorize_hypothesis", category="COMBINE", text=text[:80])
        return FEECCategory.COMBINE

    log.debug("categorize_hypothesis", category="EXPLORE", text=text[:80])
    return FEECCategory.EXPLORE


def rank_hypotheses(hypotheses: list[dict]) -> list[dict]:
    """Sort *hypotheses* by FEEC priority (Fix > Exploit > Explore > Combine).

    Each dict must contain a ``"description"`` key whose value is used for
    categorization.  A ``"category"`` key is injected (or overwritten) with
    the resolved :class:`FEECCategory` name.

    The sort is **stable**: hypotheses in the same category keep their
    original relative order.
    """
    for h in hypotheses:
        cat = categorize_hypothesis(h.get("description", ""))
        h["category"] = cat.name
    ranked = sorted(hypotheses, key=lambda h: FEECCategory[h["category"]].value)
    log.info(
        "rank_hypotheses",
        count=len(ranked),
        order=[h["category"] for h in ranked],
    )
    return ranked


def detect_stuck(
    history: list[dict],
    threshold: int = 3,
) -> bool:
    """Return ``True`` when the last *threshold* consecutive reverts share a category.

    Each entry in *history* must have ``"verdict"`` and ``"hypothesis"`` keys.
    Only entries whose verdict is ``"revert"`` are considered consecutive; a
    ``"keep"`` verdict resets the streak.
    """
    if len(history) < threshold:
        return False

    # Walk backwards through history collecting consecutive reverts
    consecutive_reverts: list[FEECCategory] = []
    for entry in reversed(history):
        if entry.get("verdict") != "revert":
            break
        cat = categorize_hypothesis(entry.get("hypothesis", ""))
        consecutive_reverts.append(cat)

    if len(consecutive_reverts) < threshold:
        return False

    # Check if the last `threshold` reverts are all in the same category
    tail = consecutive_reverts[:threshold]
    stuck = len(set(tail)) == 1
    if stuck:
        log.warning(
            "stuck_detected",
            category=tail[0].name,
            consecutive=len(tail),
        )
    return stuck


# ── plateau detection ────────────────────────────────────────────


def detect_research_plateau(
    run_summaries: list[dict],
    threshold: int = 3,
) -> bool:
    """Return ``True`` when the last *threshold* cycles showed no metric improvement.

    *run_summaries* should be ordered oldest-first.  Each dict must contain a
    ``metric_value`` key.  Requires at least ``threshold + 1`` entries (one
    baseline plus *threshold* cycles).
    """
    if threshold <= 0:
        return False

    if len(run_summaries) < threshold + 1:
        return False

    pre_window = run_summaries[:-threshold]
    best_before = max(s["metric_value"] for s in pre_window)

    window = run_summaries[-threshold:]
    best_in_window = max(s["metric_value"] for s in window)

    plateaued = best_in_window <= best_before
    if plateaued:
        log.warning(
            "plateau_detected",
            threshold=threshold,
            best_before=best_before,
            best_in_window=best_in_window,
        )
    return plateaued


def detect_plateau(
    history: list[ExperimentRecord],
    threshold: int = 3,
) -> bool:
    """Return ``True`` if the last *threshold* consecutive experiments showed no metric improvement.

    "No improvement" means the ``score_after`` did not exceed the running best
    score at that point in the history.  Experiments without a ``score_after``
    are skipped (not counted toward the streak).

    Returns ``False`` if there are fewer than *threshold* scored experiments.
    """
    scored = [r for r in history if r.score_after is not None]
    if len(scored) < threshold:
        return False

    # Walk the scored history and compute whether each experiment improved
    # over the previous best.
    best = scored[0].score_after
    assert best is not None  # guaranteed by filter above
    no_improvement_streak = 0

    for record in scored[1:]:
        assert record.score_after is not None
        if record.score_after > best:
            best = record.score_after
            no_improvement_streak = 0
        else:
            no_improvement_streak += 1

    plateau = no_improvement_streak >= threshold
    if plateau:
        log.warning(
            "plateau_detected",
            streak=no_improvement_streak,
            threshold=threshold,
            best_score=best,
        )
    return plateau


# ── hypothesis similarity ────────────────────────────────────────


def _tokenize(text: str) -> set[str]:
    """Extract lowercase word tokens (3+ chars) from text."""
    return {w for w in text.lower().split() if len(w) >= 3}


def hypothesis_similarity(a: str, b: str) -> float:
    """Jaccard similarity between two hypothesis texts. Returns 0.0–1.0."""
    tokens_a = _tokenize(a)
    tokens_b = _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    score = len(intersection) / len(union)
    log.debug("hypothesis_similarity", score=score, a=a[:60], b=b[:60])
    return score


def find_anti_patterns(
    hypothesis: str,
    history: list[dict],
    similarity_threshold: float = 0.6,
) -> list[dict]:
    """Find reverted experiments whose hypothesis is similar to the proposed one.

    Returns a list of history entries that were reverted and have similarity
    above the threshold.  Each returned dict gets a ``"similarity"`` key added.
    """
    matches: list[dict] = []
    for entry in history:
        if entry.get("verdict") != "revert":
            continue
        past_hyp = entry.get("hypothesis", "")
        sim = hypothesis_similarity(hypothesis, past_hyp)
        if sim >= similarity_threshold:
            match = dict(entry)
            match["similarity"] = sim
            matches.append(match)
    if matches:
        log.warning(
            "anti_patterns_found",
            count=len(matches),
            hypothesis=hypothesis[:80],
        )
    return matches


# ── 3-tier experiment history ───────────────────────────────────


def _format_tier1(record: dict) -> str:
    """Full-detail block for a single experiment (Tier 1)."""
    exp_id = record.get("id", "?")
    verdict = record.get("verdict", "?")
    hypothesis = record.get("hypothesis", "")
    delta = record.get("delta")
    change_summary = record.get("change_summary", "")

    delta_str = f"{delta:+.4f}" if delta is not None else "n/a"
    cost_str = f"${record.get('cost_usd'):.2f}" if record.get("cost_usd") is not None else ""
    header = f"### Experiment {exp_id} [{verdict}] (Δ {delta_str})"
    if cost_str:
        header = f"### Experiment {exp_id} [{verdict}] (Δ {delta_str}, {cost_str})"
    lines = [
        header,
        f"**Hypothesis:** {hypothesis}",
    ]
    if change_summary:
        lines.append(f"**Changes:** {change_summary[:200]}")
    return "\n".join(lines)


def _format_tier2(record: dict) -> str:
    """One-line summary for a single experiment (Tier 2)."""
    exp_id = record.get("id", "?")
    verdict = record.get("verdict", "?")
    delta = record.get("delta")
    hypothesis = record.get("hypothesis", "")

    delta_str = f"{delta:+.4f}" if delta is not None else "n/a"
    cost_str = f" ${record.get('cost_usd'):.2f}" if record.get("cost_usd") is not None else ""
    hyp_title = hypothesis[:80]
    return f"- #{exp_id} {verdict} Δ{delta_str}{cost_str} — {hyp_title}"


def _format_tier3(records: list[dict]) -> str:
    """Aggregate stats for older experiments (Tier 3)."""
    if not records:
        return ""

    total = len(records)
    verdicts: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    deltas: list[float] = []

    for r in records:
        verdicts[r.get("verdict", "unknown")] += 1
        cat = categorize_hypothesis(r.get("hypothesis", ""))
        categories[cat.name] += 1
        d = r.get("delta")
        if d is not None:
            deltas.append(d)

    keep_count = verdicts.get("keep", 0)
    keep_rate = keep_count / total if total > 0 else 0.0

    lines = [
        f"**{total} older experiments** — {keep_rate:.0%} keep rate",
    ]

    if deltas:
        trajectory = f"score Δ range [{min(deltas):+.4f}, {max(deltas):+.4f}]"
        lines.append(f"  Score trajectory: {trajectory}")

    cat_parts = [f"{name}: {cnt}" for name, cnt in categories.most_common()]
    lines.append(f"  Categories: {', '.join(cat_parts)}")

    verdict_parts = [f"{v}: {c}" for v, c in verdicts.most_common()]
    lines.append(f"  Verdicts: {', '.join(verdict_parts)}")

    return "\n".join(lines)


def format_tiered_history(records: list[Any]) -> str:
    """Produce 3-tier experiment history output.

    - Tier 1 (last 3): full detail with hypothesis, verdict, delta, changes
    - Tier 2 (4–10 back): one-line summaries
    - Tier 3 (11+): aggregate stats only

    *records* should be ordered oldest-first (as stored in results.tsv).
    """
    if not records:
        return "No experiments recorded."

    capped = records[-MAX_INLINE_HISTORY:]
    older = records[:-MAX_INLINE_HISTORY] if len(records) > MAX_INLINE_HISTORY else []

    tier1_records = capped[-3:]
    tier2_records = capped[:-3] if len(capped) > 3 else []

    sections: list[str] = []

    sections.append(f"## Experiment History ({len(records)} total)\n")

    if older:
        sections.append("### Tier 3 — Older Experiments (aggregate)\n")
        older_dicts = [_record_to_dict(r) if not isinstance(r, dict) else r for r in older]
        sections.append(_format_tier3(older_dicts))
        sections.append("")

    if tier2_records:
        sections.append("### Tier 2 — Recent (one-line)\n")
        for r in tier2_records:
            d = _record_to_dict(r) if not isinstance(r, dict) else r
            sections.append(_format_tier2(d))
        sections.append("")

    sections.append("### Tier 1 — Latest (full detail)\n")
    for r in tier1_records:
        d = _record_to_dict(r) if not isinstance(r, dict) else r
        sections.append(_format_tier1(d))
        sections.append("")

    log.info(
        "format_tiered_history",
        total=len(records),
        tier1=len(tier1_records),
        tier2=len(tier2_records),
        tier3=len(older),
    )
    return "\n".join(sections).rstrip()


def _record_to_dict(record: object) -> dict:
    """Convert an ExperimentRecord (or any object with matching attrs) to a dict."""
    if isinstance(record, dict):
        return record
    return {
        "id": getattr(record, "id", "?"),
        "hypothesis": getattr(record, "hypothesis", ""),
        "verdict": getattr(record, "verdict", "?"),
        "delta": getattr(record, "delta", None),
        "change_summary": getattr(record, "change_summary", ""),
        "cost_usd": getattr(record, "cost_usd", None),
    }
