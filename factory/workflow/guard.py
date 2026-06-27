"""Programmatic diff guard for verified skill generation.

Compares templatized markdown (skeleton) against refined markdown
(review agent output) and verifies structural integrity.

Returns PROCEED if all checks pass, RELOOP if any structural change detected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from factory.workflow.templates import _SLOT_PATTERN

_ANNOTATION_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass
class GuardResult:
    """Result of a structural guard check."""

    verdict: str
    violations: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == "PROCEED"


def check(skeleton: str, refined: str) -> GuardResult:
    """Compare skeleton and refined templatized markdown for structural integrity.

    Four checks:
    1. All text outside {{...}} markers is byte-identical
    2. All <!-- ... --> annotation comments unchanged
    3. Command structure preserved (slot names in commands unchanged)
    4. All slot names from skeleton present in refined — none added, none removed
    """
    violations: list[str] = []

    skeleton_slots = set(name for name, _ in _SLOT_PATTERN.findall(skeleton))
    refined_slots = set(name for name, _ in _SLOT_PATTERN.findall(refined))

    added = refined_slots - skeleton_slots
    removed = skeleton_slots - refined_slots
    if added:
        violations.append(f"Slots added: {', '.join(sorted(added))}")
    if removed:
        violations.append(f"Slots removed: {', '.join(sorted(removed))}")

    skeleton_annotations = _ANNOTATION_PATTERN.findall(skeleton)
    refined_annotations = _ANNOTATION_PATTERN.findall(refined)
    if skeleton_annotations != refined_annotations:
        violations.append("Annotation comments modified")

    skeleton_stripped = _SLOT_PATTERN.sub("__SLOT__", skeleton)
    refined_stripped = _SLOT_PATTERN.sub("__SLOT__", refined)
    if skeleton_stripped != refined_stripped:
        violations.append("Text outside slot markers was modified")

    verdict = "PROCEED" if not violations else "RELOOP"
    return GuardResult(verdict=verdict, violations=violations)
