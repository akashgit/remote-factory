"""Apply structured edits to SKILL.md content."""
from __future__ import annotations

import structlog

from factory.skillopt.types import Edit, Patch

log = structlog.get_logger()

SLOW_UPDATE_START = "<!-- SLOW_UPDATE_START -->"
SLOW_UPDATE_END = "<!-- SLOW_UPDATE_END -->"
APPENDIX_START = "<!-- APPENDIX_START -->"
APPENDIX_END = "<!-- APPENDIX_END -->"

_PROTECTED_MARKERS = [
    (SLOW_UPDATE_START, SLOW_UPDATE_END),
    (APPENDIX_START, APPENDIX_END),
]


def _in_protected_region(skill: str, target: str) -> bool:
    if not target:
        return False
    target_pos = skill.find(target)
    if target_pos == -1:
        return False
    for start_marker, end_marker in _PROTECTED_MARKERS:
        s = skill.find(start_marker)
        e = skill.find(end_marker)
        if s != -1 and e != -1 and s <= target_pos < e + len(end_marker):
            return True
    return False


def _earliest_protected_pos(skill: str) -> int | None:
    positions: list[int] = []
    for start_marker, _ in _PROTECTED_MARKERS:
        pos = skill.find(start_marker)
        if pos != -1:
            positions.append(pos)
    return min(positions) if positions else None


def apply_edit(skill: str, edit: Edit) -> str:
    if edit.op != "append" and _in_protected_region(skill, edit.target):
        log.info("skipping edit in protected region", op=edit.op, target=edit.target[:50])
        return skill

    if edit.op == "append":
        protected_pos = _earliest_protected_pos(skill)
        if protected_pos is not None:
            return skill[:protected_pos] + edit.content + "\n" + skill[protected_pos:]
        return skill + "\n" + edit.content

    if edit.op == "insert_after":
        pos = skill.find(edit.target)
        if pos == -1:
            log.warning("insert_after target not found", target=edit.target[:80])
            return skill
        insert_at = pos + len(edit.target)
        return skill[:insert_at] + "\n" + edit.content + skill[insert_at:]

    if edit.op == "replace":
        if not edit.target:
            log.warning("replace edit has empty target")
            return skill
        if edit.target not in skill:
            log.warning("replace target not found", target=edit.target[:80])
            return skill
        return skill.replace(edit.target, edit.content, 1)

    if edit.op == "delete":
        if not edit.target:
            log.warning("delete edit has empty target")
            return skill
        if edit.target not in skill:
            log.warning("delete target not found", target=edit.target[:80])
            return skill
        return skill.replace(edit.target, "", 1)

    return skill


def apply_patch(skill: str, patch: Patch) -> str:
    result = skill
    for edit in patch.edits:
        result = apply_edit(result, edit)
    return result
