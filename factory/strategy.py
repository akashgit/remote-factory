"""FEEC priority heuristic — Fix, Exploit, Explore, Combine.

Categorises and ranks hypotheses so the factory always addresses the
highest-leverage category first:
  1. FIX   — resolve a crash, error, or regression
  2. EXPLOIT — build on a recent success
  3. EXPLORE — try something new
  4. COMBINE — merge prior successful approaches
"""

from __future__ import annotations

from enum import IntEnum

import structlog

log = structlog.get_logger()

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
