"""SPEC — model-readable structural map of a repository."""

from __future__ import annotations

from pathlib import Path

from factory.spec.apply_diff import apply_spec_diff
from factory.spec.generate import generate_spec
from factory.spec.ops import (
    get_impact,
    scope_diff,
    update_spec,
    validate_spec,
)


def read_spec(project_path: Path) -> str:
    """Read SPEC.md and return raw markdown content."""
    from factory.discovery.spec import resolve_spec

    spec_path = resolve_spec(project_path)
    if spec_path is None:
        raise FileNotFoundError(f"No repo spec found in {project_path}")
    return spec_path.read_text(encoding="utf-8")


__all__ = [
    "apply_spec_diff",
    "generate_spec",
    "get_impact",
    "read_spec",
    "scope_diff",
    "update_spec",
    "validate_spec",
]
