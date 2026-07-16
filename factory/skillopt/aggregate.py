"""Consolidate per-trace reflections into ranked edit proposals."""
from __future__ import annotations

import json
import re

import structlog

from factory.skillopt.models import EditProposal, TraceReflection
from factory.skillopt.reflect import _call_llm

log = structlog.get_logger()


def _extract_json_array(text: str) -> list[dict] | None:
    """Extract the first JSON array from LLM output."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass
    return None


def aggregate_reflections(
    reflections: list[TraceReflection],
    current_prompt_template: str,
) -> list[EditProposal]:
    """Consolidate per-trace reflections into edit proposals via LLM.

    Sends all reflections plus the current prompt_template to an LLM,
    which groups similar suggestions, weights by frequency, and returns
    concrete edit proposals.

    Args:
        reflections: All per-trace reflections from the reflect phase.
        current_prompt_template: The current prompt_template text.

    Returns:
        List of EditProposal objects sorted by frequency (descending).
    """
    if not reflections:
        log.warning("no reflections to aggregate")
        return []

    successes = sum(1 for r in reflections if r.resolved)
    failures = len(reflections) - successes

    reflections_json = json.dumps(
        [r.model_dump() for r in reflections],
        indent=2,
    )

    prompt = (
        f"Here are {len(reflections)} reflections from benchmark traces "
        f"({successes} successes, {failures} failures).\n"
        f"Each suggests an edit to the prompt_template.\n\n"
        f"Here is the current prompt_template:\n"
        f"<prompt_template>\n{current_prompt_template}\n</prompt_template>\n\n"
        f"Here are the reflections:\n"
        f"<reflections>\n{reflections_json[:20000]}\n</reflections>\n\n"
        f"Consolidate these into a set of concrete edit proposals. Group similar "
        f"suggestions. Weight by frequency (how many traces suggest similar edits) "
        f"and confidence scores.\n\n"
        f"Output ONLY a JSON array of EditProposal objects:\n"
        f"[\n"
        f"  {{\n"
        f'    "edit_type": "add_rule|modify_rule|remove_rule|reword_section",\n'
        f'    "location": "which section/rule in the prompt_template",\n'
        f'    "original_text": "text to replace (empty string for add)",\n'
        f'    "proposed_text": "new text",\n'
        f'    "rationale": "why, citing N traces that support this",\n'
        f'    "supporting_instances": ["instance_id_1", ...],\n'
        f'    "frequency": N\n'
        f"  }}\n"
        f"]\n"
    )

    raw = _call_llm(prompt, timeout=240)
    if not raw:
        log.warning("LLM returned no output for aggregation")
        return []

    parsed = _extract_json_array(raw)
    if not parsed:
        log.warning("failed to parse LLM JSON array", raw=raw[:200])
        return []

    proposals: list[EditProposal] = []
    for item in parsed:
        try:
            proposals.append(EditProposal(
                edit_type=item.get("edit_type", "modify_rule"),
                location=item.get("location", ""),
                original_text=item.get("original_text", ""),
                proposed_text=item.get("proposed_text", ""),
                rationale=item.get("rationale", ""),
                supporting_instances=item.get("supporting_instances", []),
                frequency=int(item.get("frequency", 1)),
            ))
        except Exception as exc:
            log.warning("skipping invalid proposal", error=str(exc))

    proposals.sort(key=lambda p: p.frequency, reverse=True)
    log.info("aggregation complete", proposals=len(proposals))
    return proposals
