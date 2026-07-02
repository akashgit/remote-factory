"""GRAPH-SPEC — model-readable structural map of a repository."""

from __future__ import annotations

from pathlib import Path

from factory.spec.generate import collect_source_files, generate_spec, group_into_batches
from factory.spec.impact import get_impact
from factory.spec.update import DiffScope, scope_diff, update_spec
from factory.spec.validate import ValidationResult, validate_spec


def read_spec(project_path: Path) -> str:
    """Read GRAPH-SPEC.md and return raw markdown content."""
    from factory.discovery.spec import resolve_spec

    spec_path = resolve_spec(project_path)
    if spec_path is None:
        raise FileNotFoundError(f"No repo spec found in {project_path}")
    return spec_path.read_text(encoding="utf-8")


__all__ = [
    "DiffScope",
    "ValidationResult",
    "collect_source_files",
    "generate_spec",
    "get_impact",
    "group_into_batches",
    "read_spec",
    "scope_diff",
    "update_spec",
    "validate_spec",
]
