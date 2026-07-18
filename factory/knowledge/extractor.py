"""Knowledge extraction — convert agent logs and tool traces into triplets."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import structlog

from factory.knowledge.models import (
    Entity,
    EntityType,
    PredicateType,
    Triplet,
    _short_uuid,
)

log = structlog.get_logger()


# ── extraction result ────────────────────────────────────────────


class _ExtractionResult:
    """Container for extraction output (not persisted, used in-process)."""

    __slots__ = ("triplets", "raw_response", "source_label")

    def __init__(
        self,
        triplets: list[Triplet],
        raw_response: str = "",
        source_label: str = "",
    ) -> None:
        self.triplets = triplets
        self.raw_response = raw_response
        self.source_label = source_label


# ── deterministic extraction ─────────────────────────────────────


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_")


def _make_entity(entity_type: EntityType, name: str, **attrs: str | float | bool) -> Entity:
    slug = _slugify(name)
    return Entity(
        id=f"{entity_type.value}:{slug}",
        type=entity_type,
        name=name,
        attributes=dict(attrs) if attrs else {},
    )


def extract_from_tool_calls(
    tool_calls: list[dict[str, object]],
    task_context: str,
    *,
    agent_name: str = "agent",
    source_label: str = "tool_trace",
) -> _ExtractionResult:
    """Extract triplets from a structured list of tool calls.

    Each tool_call dict should have: name, arguments (optional), result (optional),
    error (optional), success (optional bool).
    """
    agent = _make_entity(EntityType.AGENT, agent_name)
    task = _make_entity(EntityType.TASK, task_context)
    now = datetime.now()
    triplets: list[Triplet] = []
    prev_action: Entity | None = None
    has_failure = False

    for i, call in enumerate(tool_calls):
        tool_name = str(call.get("name", f"unknown_tool_{i}"))
        tool = _make_entity(EntityType.TOOL, tool_name)
        action = _make_entity(
            EntityType.ACTION,
            f"{tool_name}_call_{i}",
            index=float(i),
        )

        triplets.append(
            Triplet(
                subject=agent,
                predicate=PredicateType.CALLS,
                object=tool,
                source=source_label,
                timestamp=now,
                evidence=f"Tool call #{i}: {tool_name}",
            )
        )

        if prev_action is not None:
            triplets.append(
                Triplet(
                    subject=prev_action,
                    predicate=PredicateType.PRECEDES,
                    object=action,
                    source=source_label,
                    timestamp=now,
                )
            )

        error = call.get("error")
        success = call.get("success", error is None)

        if error:
            has_failure = True
            error_entity = _make_entity(
                EntityType.ERROR,
                str(error)[:80],
                tool=tool_name,
            )
            triplets.append(
                Triplet(
                    subject=action,
                    predicate=PredicateType.FAILS_WITH,
                    object=error_entity,
                    source=source_label,
                    timestamp=now,
                    evidence=str(error),
                )
            )
        elif success:
            result_val = call.get("result", "")
            outcome = _make_entity(
                EntityType.OUTCOME,
                f"{tool_name}_result_{i}",
                success=True,
            )
            triplets.append(
                Triplet(
                    subject=action,
                    predicate=PredicateType.PRODUCES,
                    object=outcome,
                    source=source_label,
                    timestamp=now,
                    evidence=str(result_val)[:200] if result_val else "",
                )
            )

        prev_action = action

    if has_failure:
        triplets.append(
            Triplet(
                subject=agent,
                predicate=PredicateType.FAILS_AT,
                object=task,
                source=source_label,
                timestamp=now,
            )
        )
    else:
        triplets.append(
            Triplet(
                subject=agent,
                predicate=PredicateType.SUCCEEDS_AT,
                object=task,
                source=source_label,
                timestamp=now,
            )
        )

    return _ExtractionResult(
        triplets=triplets,
        source_label=source_label,
    )


# ── LLM-driven extraction ───────────────────────────────────────

EXTRACTION_PROMPT = """\
You are a knowledge extraction agent. Extract structured facts from the \
following agent execution log as knowledge graph triplets.

## Entity types
{entity_types}

## Predicate types
{predicate_types}

## Task context
{task_context}

## Rules
- Entity IDs must follow the format "type:snake_case_name" (e.g., "tool:get_order")
- Assign confidence 1.0 for directly observed facts, 0.7-0.9 for inferences
- Include evidence: the relevant snippet from the log
- Extract at most {max_triplets} triplets
- Focus on behavioral patterns: what the agent does, what fails, what causes failures

## Log content
```
{content}
```

## Output format
Return a JSON array of triplet objects:
```json
[
  {{
    "subject": {{"id": "type:name", "type": "type_enum", "name": "Human Name"}},
    "predicate": "predicate_enum",
    "object": {{"id": "type:name", "type": "type_enum", "name": "Human Name"}},
    "confidence": 0.9,
    "evidence": "relevant log snippet"
  }}
]
```

Return ONLY the JSON array, no other text.
"""


def build_extraction_prompt(
    content: str,
    task_context: str,
    max_triplets: int = 50,
) -> str:
    """Build the LLM prompt for triplet extraction."""
    return EXTRACTION_PROMPT.format(
        entity_types=", ".join(e.value for e in EntityType),
        predicate_types=", ".join(p.value for p in PredicateType),
        task_context=task_context,
        max_triplets=max_triplets,
        content=content[:10000],
    )


def parse_extraction_response(
    response: str,
    source_label: str = "llm_extraction",
) -> list[Triplet]:
    """Parse LLM JSON response into validated Triplet objects."""
    raw = _extract_json(response)
    if raw is None:
        log.warning("extraction_no_json_found", response_len=len(response))
        return []

    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("extraction_json_parse_failed", error=str(exc))
        return []

    if not isinstance(items, list):
        items = [items]

    now = datetime.now()
    triplets: list[Triplet] = []
    for item in items:
        try:
            triplet = _item_to_triplet(item, source_label, now)
            if triplet is not None:
                triplets.append(triplet)
        except (KeyError, ValueError, TypeError) as exc:
            log.debug("extraction_item_skipped", error=str(exc), item=str(item)[:200])

    log.info(
        "extraction_parsed",
        total_items=len(items),
        valid_triplets=len(triplets),
    )
    return triplets


def _extract_json(text: str) -> str | None:
    """Extract JSON from response, trying code fences first, then raw."""
    fence = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()

    for start in ("[", "{"):
        idx = text.find(start)
        if idx >= 0:
            return text[idx:]

    return None


def _resolve_predicate(value: str) -> PredicateType:
    try:
        return PredicateType(value)
    except ValueError:
        return PredicateType.RELATED_TO


def _resolve_entity_type(value: str) -> EntityType:
    try:
        return EntityType(value)
    except ValueError:
        return EntityType.CONCEPT


def _item_to_triplet(
    item: dict[str, object],
    source_label: str,
    timestamp: datetime,
) -> Triplet | None:
    """Convert a raw dict from LLM output to a validated Triplet."""
    subj_raw = item.get("subject")
    obj_raw = item.get("object")
    pred_raw = item.get("predicate")

    if not isinstance(subj_raw, dict) or not isinstance(obj_raw, dict) or not pred_raw:
        return None

    subject = Entity(
        id=str(subj_raw.get("id", f"concept:{_short_uuid()}")),
        type=_resolve_entity_type(str(subj_raw.get("type", "concept"))),
        name=str(subj_raw.get("name", subj_raw.get("id", "unknown"))),
    )
    obj = Entity(
        id=str(obj_raw.get("id", f"concept:{_short_uuid()}")),
        type=_resolve_entity_type(str(obj_raw.get("type", "concept"))),
        name=str(obj_raw.get("name", obj_raw.get("id", "unknown"))),
    )

    confidence = float(str(item.get("confidence", 0.8)))
    confidence = max(0.0, min(1.0, confidence))

    return Triplet(
        subject=subject,
        predicate=_resolve_predicate(str(pred_raw)),
        object=obj,
        confidence=confidence,
        source=source_label,
        timestamp=timestamp,
        evidence=str(item.get("evidence", ""))[:500],
    )


async def extract_triplets_from_log(
    log_content: str,
    task_context: str,
    project_path: Path,
    *,
    source_label: str = "llm_extraction",
    max_triplets: int = 50,
) -> _ExtractionResult:
    """Extract knowledge triplets from agent execution log text via LLM.

    Invokes a researcher agent with the extraction prompt and parses the response.
    """
    from factory.agents.runner import invoke_agent

    prompt = build_extraction_prompt(log_content, task_context, max_triplets)
    stdout, rc = await invoke_agent(
        role="researcher",
        task=prompt,
        project_path=project_path,
        timeout=120.0,
    )

    triplets = parse_extraction_response(stdout, source_label)
    return _ExtractionResult(
        triplets=triplets,
        raw_response=stdout,
        source_label=source_label,
    )


async def extract_from_diff(
    expected_output: str,
    actual_output: str,
    task_context: str,
    project_path: Path,
    *,
    source_label: str = "diff_extraction",
) -> _ExtractionResult:
    """Extract triplets from expected vs. actual behavior differences."""
    diff_content = (
        f"EXPECTED OUTPUT:\n{expected_output}\n\n"
        f"ACTUAL OUTPUT:\n{actual_output}\n\n"
        f"Analyze the differences and extract triplets about what went wrong."
    )
    return await extract_triplets_from_log(
        diff_content,
        task_context,
        project_path,
        source_label=source_label,
    )
