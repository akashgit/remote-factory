"""Known-fixes database — lookup fixes and dead-ends from YAML config."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

import yaml


def load_known_fixes(path: str | Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def match_pattern(pattern: str, text: str) -> bool:
    """Check whether a fix pattern (regex) matches anywhere in the text."""
    return bool(re.search(pattern, text))


def lookup_fix(
    db: dict[str, Any],
    error_text: str,
    *,
    project: str | None = None,
    version_tag: str | None = None,
) -> dict[str, Any] | None:
    """Find the first matching fix for an error string.

    Project-specific fixes are checked before universal fixes.
    Version glob filtering via applies_to when version_tag is provided.
    """
    candidates: list[dict[str, Any]] = []

    if project:
        project_data = db.get("projects", {}).get(project, {})
        candidates.extend(project_data.get("fixes", []))

    candidates.extend(db.get("universal", {}).get("fixes", []))

    for fix in candidates:
        if not match_pattern(fix["pattern"], error_text):
            continue
        if version_tag and "applies_to" in fix:
            if not fnmatch.fnmatch(version_tag, fix["applies_to"]):
                continue
        return fix

    return None


def is_dead_end(
    db: dict[str, Any],
    artifact: str,
    *,
    project: str | None = None,
    version_tag: str | None = None,
) -> dict[str, Any] | None:
    """Check if an artifact is registered as a dead-end.

    Returns the dead-end entry if found, None otherwise.
    Project-specific dead-ends are checked before universal ones.
    """
    candidates: list[dict[str, Any]] = []

    if project:
        project_data = db.get("projects", {}).get(project, {})
        candidates.extend(project_data.get("dead_ends", []))

    candidates.extend(db.get("universal", {}).get("dead_ends", []))

    for entry in candidates:
        if entry["artifact"] == artifact:
            if version_tag and "applies_to" in entry:
                if not fnmatch.fnmatch(version_tag, entry["applies_to"]):
                    continue
            return entry

    return None
