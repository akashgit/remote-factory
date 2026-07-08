"""SPEC resolution and generation for the discovery pipeline."""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

log = structlog.get_logger()


def resolve_spec(project_path: Path) -> Path | None:
    """Locate SPEC.md at the project root or .factory/. Returns None if absent."""
    spec = project_path / "SPEC.md"
    if spec.exists():
        log.debug("resolve_spec", path=str(spec))
        return spec
    factory_spec = project_path / ".factory" / "SPEC.md"
    if factory_spec.exists():
        log.debug("resolve_spec", path=str(factory_spec))
        return factory_spec
    log.debug("resolve_spec", source="absent")
    return None


def generate_spec(project_path: Path) -> str:
    """Generate a SPEC by delegating to the agent-driven pipeline.

    Wraps the async factory.spec.generate.generate_spec() for sync callers.
    Returns the spec content as a string.
    """
    from factory.spec.generate import generate_spec as _generate_spec

    spec_path = asyncio.run(_generate_spec(project_path))
    return spec_path.read_text()
