"""LLM-driven edit ranking and selection."""
from __future__ import annotations

import json
import re
from pathlib import Path

import structlog

from factory.skillopt.reflect import _call_llm
from factory.skillopt.types import Edit, Patch

log = structlog.get_logger()

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text()


def rank_and_select(skill_content: str, patch: Patch, max_edits: int = 3) -> Patch:
    if len(patch.edits) <= max_edits:
        return patch

    template = _load_prompt("ranking.md")
    patch_json = json.dumps(patch.model_dump(), indent=2)
    prompt = (
        template
        .replace("{{SKILL_CONTENT}}", skill_content)
        .replace("{{PATCH}}", patch_json)
        .replace("{{MAX_EDITS}}", str(max_edits))
    )

    raw = _call_llm(prompt, timeout=300)
    if not raw:
        log.warning("ranking LLM failed, falling back to truncation")
        return Patch(
            edits=patch.edits[:max_edits],
            reasoning=patch.reasoning,
        )

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        log.warning("ranking LLM parse failed, falling back to truncation")
        return Patch(
            edits=patch.edits[:max_edits],
            reasoning=patch.reasoning,
        )

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        log.warning("ranking JSON decode failed, falling back to truncation")
        return Patch(
            edits=patch.edits[:max_edits],
            reasoning=patch.reasoning,
        )

    edits = [
        Edit(
            op=e.get("op", "append"),
            content=e.get("content", ""),
            target=e.get("target", ""),
            support_count=e.get("support_count"),
            source_type=e.get("source_type"),
        )
        for e in data.get("edits", [])
    ][:max_edits]

    return Patch(
        edits=edits,
        reasoning=data.get("reasoning", patch.reasoning),
        ranking_details=data.get("ranking_details"),
    )
