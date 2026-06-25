"""Parse strategy plan (current.md) into structured hypothesis objects."""

from __future__ import annotations

import re
from dataclasses import dataclass


_HEADER_RE = re.compile(r"^####\s+(H\d+):\s*(.+)$", re.MULTILINE)
_FIELD_RE = re.compile(r"^-\s+\*\*(.+?):\*\*\s*(.*)")


@dataclass
class ParsedHypothesis:
    id: str
    title: str
    category: str = ""
    what: str = ""
    expected_impact: str = ""
    growth_dimension: str = ""
    type: str = ""
    priority: str = ""


def parse_strategy_plan(content: str) -> list[ParsedHypothesis]:
    headers = list(_HEADER_RE.finditer(content))
    if not headers:
        return []

    results: list[ParsedHypothesis] = []
    for i, match in enumerate(headers):
        h_id = match.group(1)
        title = match.group(2).strip()

        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(content)
        block = content[start:end]

        fields = _extract_fields(block)

        results.append(ParsedHypothesis(
            id=h_id,
            title=title,
            category=fields.get("Category", ""),
            what=fields.get("What", ""),
            expected_impact=fields.get("Expected impact", ""),
            growth_dimension=fields.get("Growth dimension", ""),
            type=fields.get("Type", ""),
            priority=fields.get("Priority", ""),
        ))

    return results


def _extract_fields(block: str) -> dict[str, str]:
    """Extract - **FieldName:** value entries from a hypothesis block."""
    fields: dict[str, str] = {}
    current_field: str | None = None
    current_lines: list[str] = []

    for line in block.split("\n"):
        if line.startswith("### ") or line.startswith("#### "):
            break

        field_match = _FIELD_RE.match(line)
        if field_match:
            if current_field is not None:
                fields[current_field] = "\n".join(current_lines).strip()
            current_field = field_match.group(1)
            value = field_match.group(2).strip()
            current_lines = [value] if value else []
        elif current_field is not None and line.startswith("  "):
            current_lines.append(line)
        elif line.strip() == "":
            continue
        else:
            if current_field is not None:
                fields[current_field] = "\n".join(current_lines).strip()
                current_field = None
                current_lines = []

    if current_field is not None:
        fields[current_field] = "\n".join(current_lines).strip()

    return fields
