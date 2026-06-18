"""Curator — deduplicate, score, prune, and merge playbook bullets.

Follows the same three-phase pruning pattern as the WXO ACE implementation:
  1. Net-negative removal (harmful > helpful with enough observations)
  2. Semantic deduplication (SequenceMatcher, threshold 0.75)
  3. Capacity capping (sort by net score, keep top N)
"""

from __future__ import annotations

from difflib import SequenceMatcher

import structlog

from factory.ace.models import Playbook, PlaybookItem

log = structlog.get_logger()

# Similarity threshold for dedup (same as WXO ACE)
_SIMILARITY_THRESHOLD = 0.75

# Minimum observations before pruning net-negative items
_MIN_OBSERVATIONS = 3


def _similarity(a: str, b: str) -> float:
    """Compute string similarity ratio."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _merge_counters(keep: PlaybookItem, remove: PlaybookItem) -> PlaybookItem:
    """Merge counters from remove into keep, preserving keep's content."""
    return PlaybookItem(
        id=keep.id,
        content=keep.content,
        helpful=keep.helpful + remove.helpful,
        harmful=keep.harmful + remove.harmful,
        section=keep.section,
    )


def _reassign_ids(items: list[PlaybookItem], role: str) -> list[PlaybookItem]:
    """Reassign sequential IDs to all items."""
    prefix_map = {
        "strategist": "strat",
        "builder": "build",
        "evaluator": "eval",
        "researcher": "res",
        "reviewer": "rev",
        "archivist": "arch",
    }
    prefix = prefix_map.get(role, role[:5])
    result = []
    for i, item in enumerate(items, 1):
        result.append(PlaybookItem(
            id=f"{prefix}-{i:05d}",
            content=item.content,
            helpful=item.helpful,
            harmful=item.harmful,
            section=item.section,
        ))
    return result


def curate_playbook(
    existing: Playbook,
    candidates: list[PlaybookItem],
    max_items: int = 15,
) -> Playbook:
    """Merge candidates into existing playbook, then prune.

    Three-phase process:
    1. Merge candidates (dedup against existing, add new)
    2. Remove net-negative items (harmful > helpful, enough observations)
    3. Cap at max_items by net score

    Args:
        existing: Current playbook for this role.
        candidates: New candidate bullets from the reflector.
        max_items: Maximum bullets to keep per playbook.

    Returns:
        Updated Playbook with merged, pruned items.
    """
    items = list(existing.items)

    # Phase 0: Merge candidates into existing
    for candidate in candidates:
        # Check for semantic duplicate in existing items
        merged = False
        for i, item in enumerate(items):
            if _similarity(candidate.content, item.content) >= _SIMILARITY_THRESHOLD:
                # Merge counters into the existing item
                items[i] = _merge_counters(item, candidate)
                merged = True
                log.debug("curator_merge", existing=item.id, candidate=candidate.id)
                break
        if not merged:
            items.append(candidate)

    # Phase 1: Remove net-negative items
    # Two criteria:
    #   a) harmful exceeds helpful by 3+ (strong signal of bad advice)
    #   b) Legacy: harmful > helpful with enough observations
    before_count = len(items)
    items = [
        item for item in items
        if not (
            (item.harmful - item.helpful >= 3)
            or (
                item.harmful > item.helpful
                and (item.helpful + item.harmful) >= _MIN_OBSERVATIONS
            )
        )
    ]
    removed = before_count - len(items)
    if removed:
        log.info("curator_prune_negative", removed=removed)

    # Phase 2: Semantic deduplication among remaining items
    deduped: list[PlaybookItem] = []
    for item in items:
        duplicate_found = False
        for j, existing_item in enumerate(deduped):
            if _similarity(item.content, existing_item.content) >= _SIMILARITY_THRESHOLD:
                # Keep the one with higher net score
                if item.net_score > existing_item.net_score:
                    deduped[j] = _merge_counters(item, existing_item)
                else:
                    deduped[j] = _merge_counters(existing_item, item)
                duplicate_found = True
                break
        if not duplicate_found:
            deduped.append(item)

    if len(deduped) < len(items):
        log.info("curator_dedup", before=len(items), after=len(deduped))
    items = deduped

    # Phase 3: Cap at max_items by net score
    if len(items) > max_items:
        items.sort(key=lambda i: i.net_score, reverse=True)
        items = items[:max_items]
        log.info("curator_cap", max=max_items, kept=len(items))

    # Reassign sequential IDs
    items = _reassign_ids(items, existing.role)

    return Playbook(role=existing.role, items=items)
