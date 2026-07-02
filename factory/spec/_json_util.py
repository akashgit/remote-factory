"""JSON extraction from agent stdout."""

from __future__ import annotations

import json
import re


def extract_json(text: str) -> dict | list:
    """Extract the first JSON object or array from agent output.

    Raises ValueError when no valid JSON is found.
    """
    text = text.strip()

    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n```\s*$", "", text, flags=re.MULTILINE)

    # Try object first (most common for structured results)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    # Try array
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    # Last resort: parse entire text
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"No valid JSON found in agent output: {exc}") from exc
