"""Textual learning rate — rank and filter edit proposals."""
from __future__ import annotations

import structlog

from factory.skillopt.models import EditProposal

log = structlog.get_logger()


def select_edits(
    proposals: list[EditProposal],
    learning_rate: int = 3,
    max_text_change_pct: float = 0.2,
    prompt_template_length: int | None = None,
) -> list[EditProposal]:
    """Apply textual learning rate to bound edit magnitude.

    Ranks proposals by frequency, takes the top K (learning_rate), and
    rejects any proposal whose text change exceeds max_text_change_pct
    of the prompt_template length.

    Args:
        proposals: Edit proposals from the aggregation phase.
        learning_rate: Maximum number of edits to accept per cycle.
        max_text_change_pct: Maximum fraction of prompt_template text
            that a single edit may change (0.0 to 1.0).
        prompt_template_length: Length of the current prompt_template
            in characters. If None, the magnitude filter is skipped.

    Returns:
        Selected proposals, ordered by frequency descending.
    """
    if not proposals:
        return []

    ranked = sorted(proposals, key=lambda p: p.frequency, reverse=True)

    selected: list[EditProposal] = []
    for proposal in ranked:
        if len(selected) >= learning_rate:
            break

        if prompt_template_length and prompt_template_length > 0:
            change_size = max(len(proposal.proposed_text), len(proposal.original_text))
            change_pct = change_size / prompt_template_length
            if change_pct > max_text_change_pct:
                log.info(
                    "rejecting proposal — exceeds text change limit",
                    location=proposal.location,
                    change_pct=round(change_pct, 2),
                    max_pct=max_text_change_pct,
                )
                continue

        selected.append(proposal)

    log.info(
        "edit selection complete",
        considered=len(proposals),
        selected=len(selected),
        learning_rate=learning_rate,
    )
    return selected
