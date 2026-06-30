"""SPEC resolution and generation for the discovery pipeline."""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

log = structlog.get_logger()


def resolve_spec(project_path: Path) -> tuple[Path | None, str]:
    """Locate an existing GRAPH-SPEC.md — committed (project root) takes priority over generated (.factory/).

    Falls back to SPEC.md for backward compatibility with older specs.
    """
    for name in ("GRAPH-SPEC.md", "SPEC.md"):
        committed = project_path / name
        if committed.exists():
            log.debug("resolve_spec", source="committed", path=str(committed))
            return committed, "committed"

        generated = project_path / ".factory" / name
        if generated.exists():
            log.debug("resolve_spec", source="generated", path=str(generated))
            return generated, "generated"

    log.debug("resolve_spec", source="absent")
    return None, "absent"


def generate_spec(project_path: Path) -> str:
    """Generate a SPEC by delegating to the agent-driven pipeline.

    Wraps the async factory.spec.generate.generate_spec() for sync callers.
    Returns the spec content as a string.
    """
    from factory.spec.generate import generate_spec as _generate_spec

    spec_path = asyncio.run(_generate_spec(project_path))
    return spec_path.read_text()
