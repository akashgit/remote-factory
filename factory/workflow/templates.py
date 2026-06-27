"""Template slot format for verified skill generation.

Slot format: {{slot_name::default_value}}

- emit(name, value) → produces '{{name::value}}'
- resolve(text) → strips markers, emits bare values as clean prose
- extract(text) → returns list of (name, value) tuples from a templatized string
"""

from __future__ import annotations

import re

_SLOT_PATTERN = re.compile(r"\{\{([a-z_][a-z0-9_]*)::(.*?)\}\}", re.DOTALL)


def emit(slot_name: str, default_value: str) -> str:
    """Produce a template slot marker: {{slot_name::default_value}}."""
    return f"{{{{{slot_name}::{default_value}}}}}"


def resolve(text: str) -> str:
    """Strip slot markers, emitting bare default values as clean prose."""
    return _SLOT_PATTERN.sub(r"\2", text)


def extract(text: str) -> list[tuple[str, str]]:
    """Extract all (slot_name, value) tuples from templatized text."""
    return _SLOT_PATTERN.findall(text)
