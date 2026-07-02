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
    text = text.strip()

    # Try full text first (handles clean agent output)
    try:
        result = json.loads(text)
        if isinstance(result, (dict, list)):
            return result
    except json.JSONDecodeError:
        pass

    # Try whichever delimiter appears first — avoids extracting an inner
    # object when the outermost structure is an array
    obj_start = text.find("{")
    arr_start = text.find("[")

    attempts: list[tuple[str, str]] = []
    if obj_start >= 0 and arr_start >= 0:
        if arr_start < obj_start:
            attempts = [("[", "]"), ("{", "}")]
        else:
            attempts = [("{", "}"), ("[", "]")]
    elif obj_start >= 0:
        attempts = [("{", "}")]
    elif arr_start >= 0:
        attempts = [("[", "]")]

    for open_char, close_char in attempts:
        start = text.find(open_char)
        end = text.rfind(close_char)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

    raise ValueError("No valid JSON found in agent output")
