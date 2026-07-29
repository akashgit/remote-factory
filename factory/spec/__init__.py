"""SPEC — model-readable structural map of a repository."""

from __future__ import annotations

from pathlib import Path

import structlog

from factory.spec.apply_diff import apply_spec_diff
from factory.spec.generate import collect_source_files, generate_spec, group_into_batches
from factory.spec.ops import (
    get_impact,
    scope_diff,
    update_spec,
    validate_spec,
)

log = structlog.get_logger()


def read_spec(project_path: Path) -> str:
    """Read SPEC.md and return raw markdown content."""
    log.info("read_spec.start", project=str(project_path))
    from factory.discovery.spec import resolve_spec

    spec_path = resolve_spec(project_path)
    if spec_path is None:
        log.error("read_spec.error", reason="spec_not_found", project=str(project_path))
        raise FileNotFoundError(f"No repo spec found in {project_path}")
    log.info("read_spec.done", spec_path=str(spec_path))
    return spec_path.read_text(encoding="utf-8")


__all__ = [
    "apply_spec_diff",
    "collect_source_files",
    "generate_spec",
    "get_impact",
    "group_into_batches",
    "read_spec",
    "scope_diff",
    "update_spec",
    "validate_spec",
]
