"""Scope lock for interactive mode specs — prevents silent overwrites during build."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pydantic
import structlog

from factory.models import SpecLock

log = structlog.get_logger()

_SPEC_LOCK_FILE = "spec_lock.json"


def create_spec_lock(
    project_path: Path,
    spec_content: str,
    scope_boundaries: list[str],
    source: Literal["interactive", "research"] = "interactive",
) -> SpecLock:
    """Write a spec lock after the user approves a spec in interactive mode."""
    from factory.store import ensure_factory_dir

    spec_hash = hashlib.sha256(spec_content.encode()).hexdigest()
    lock = SpecLock(
        spec_hash=spec_hash,
        scope_boundaries=scope_boundaries,
        locked_at=datetime.now(timezone.utc).isoformat(),
        source=source,
    )
    factory_dir = project_path / ".factory"
    ensure_factory_dir(factory_dir)
    lock_path = factory_dir / _SPEC_LOCK_FILE
    lock_path.write_text(lock.model_dump_json(indent=2))
    log.info("spec_lock.created", path=str(lock_path), source=source)
    return lock


def read_spec_lock(project_path: Path) -> SpecLock | None:
    """Load spec lock from .factory/spec_lock.json, or None if absent/corrupt."""
    lock_path = project_path / ".factory" / _SPEC_LOCK_FILE
    if not lock_path.exists():
        log.debug("spec_lock.not_found", path=str(lock_path))
        return None
    try:
        data = json.loads(lock_path.read_text())
        lock = SpecLock.model_validate(data)
    except (json.JSONDecodeError, pydantic.ValidationError) as exc:
        log.warning("spec_lock.corrupt", path=str(lock_path), error=str(exc))
        return None
    log.info("spec_lock.loaded", path=str(lock_path))
    return lock


def clear_spec_lock(project_path: Path) -> None:
    """Remove spec lock file after build completes or user requests it."""
    lock_path = project_path / ".factory" / _SPEC_LOCK_FILE
    if lock_path.exists():
        lock_path.unlink()
        log.info("spec_lock.cleared", path=str(lock_path))
    else:
        log.debug("spec_lock.clear_noop", path=str(lock_path))


def check_scope_deviation(lock: SpecLock, proposed_scope: list[str]) -> list[str]:
    """Return items in proposed_scope that fall outside the locked scope boundaries.

    Each locked boundary is compared as a prefix — a proposed scope item is
    within bounds if it starts with any locked boundary.  Items that do not
    match any boundary are returned as deviations.
    """
    deviations: list[str] = []
    for item in proposed_scope:
        if not any(item.startswith(boundary) for boundary in lock.scope_boundaries):
            deviations.append(item)
    return deviations
