"""Extract structured solutions from LLM text output."""

from __future__ import annotations

import json
import re
from typing import Any


def parse_solution(text: str, task_type: str = "circle-packing") -> dict[str, Any] | None:
    """Parse a structured solution from LLM output.

    Tries multiple extraction strategies in order:
    1. Fenced JSON code block (```json ... ```)
    2. Fenced plain code block (``` ... ```)
    3. Bare JSON object in text

    Returns None if no valid solution can be extracted.
    """
    extracted = (
        _extract_fenced_json(text)
        or _extract_fenced_block(text)
        or _extract_bare_json(text)
    )

    if extracted is None:
        return None

    if not isinstance(extracted, dict):
        return None

    return _validate_for_task(extracted, task_type)


def _extract_fenced_json(text: str) -> Any | None:
    """Extract JSON from ```json ... ``` blocks (last match wins)."""
    matches = re.findall(r"```json\s*\n?([\s\S]*?)\n?\s*```", text)
    if not matches:
        return None
    for candidate in reversed(matches):
        try:
            return json.loads(candidate.strip())
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _extract_fenced_block(text: str) -> Any | None:
    """Extract JSON from ``` ... ``` blocks (non-json fenced)."""
    matches = re.findall(r"```(?!json|python|py)(\w*)\s*\n?([\s\S]*?)\n?\s*```", text)
    if not matches:
        return None
    for _, candidate in reversed(matches):
        candidate = candidate.strip()
        if candidate.startswith("{"):
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                continue
    return None


def _extract_bare_json(text: str) -> Any | None:
    """Extract a bare JSON object from text using brace matching."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    return None
    return None


def _validate_for_task(data: dict[str, Any], task_type: str) -> dict[str, Any] | None:
    """Task-specific validation. Returns the data if valid, None otherwise."""
    if task_type == "circle-packing":
        return _validate_circle_packing(data)
    if "solution" in data:
        return data
    return data


def _validate_circle_packing(data: dict[str, Any]) -> dict[str, Any] | None:
    """Validate circle-packing solution: {"circles": [[x, y, r], ...]}."""
    circles = data.get("circles")
    if circles is None:
        if "solution" in data and isinstance(data["solution"], dict):
            circles = data["solution"].get("circles")
            if circles is not None:
                data = {"circles": circles}
            else:
                return None
        else:
            return None

    if not isinstance(circles, list):
        return None

    for circle in circles:
        if not isinstance(circle, (list, tuple)) or len(circle) != 3:
            return None
        if not all(isinstance(v, (int, float)) for v in circle):
            return None

    return data
