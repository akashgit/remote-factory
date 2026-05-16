"""Global project registry — tracks factory-managed projects at ~/.factory/registry.json."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import structlog

from factory.models import ProjectEntry, ProjectRegistry

log = structlog.get_logger()


def _default_registry_path() -> Path:
    """Return registry path, respecting FACTORY_REGISTRY_DIR override for testing."""
    override = os.environ.get("FACTORY_REGISTRY_DIR")
    base = Path(override) if override else Path.home() / ".factory"
    return base / "registry.json"


def _parse_registry_datetimes(data: dict) -> None:
    """Convert ISO datetime strings to datetime objects for strict Pydantic models."""
    if "updated_at" in data and isinstance(data["updated_at"], str):
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
    for entry in data.get("projects", []):
        for key in ("registered_at", "last_experiment_at"):
            val = entry.get(key)
            if isinstance(val, str):
                entry[key] = datetime.fromisoformat(val)


def _load_registry(path: Path | None = None) -> ProjectRegistry:
    """Load the registry from disk, returning empty registry if missing/corrupt."""
    registry_path = path or _default_registry_path()
    if not registry_path.exists():
        return ProjectRegistry(projects=[], updated_at=datetime.now())
    try:
        data = json.loads(registry_path.read_text())
        _parse_registry_datetimes(data)
        return ProjectRegistry(**data)
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        log.warning("registry_load_failed", path=str(registry_path), error=str(exc))
        return ProjectRegistry(projects=[], updated_at=datetime.now())


def _save_registry(registry: ProjectRegistry, path: Path | None = None) -> None:
    """Atomically save the registry to disk."""
    registry_path = path or _default_registry_path()
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry.updated_at = datetime.now()
    tmp_path = registry_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(registry.model_dump(), indent=2, default=str) + "\n")
    tmp_path.replace(registry_path)
    log.debug("registry_saved", projects=len(registry.projects))


def register_project(project_path: Path, registry_path: Path | None = None) -> None:
    """Register a project. Idempotent — updates path if name already exists."""
    resolved = project_path.resolve()
    registry = _load_registry(registry_path)

    for entry in registry.projects:
        if entry.path == str(resolved):
            log.debug("register_project_exists", path=str(resolved))
            return

    entry = ProjectEntry(
        path=str(resolved),
        name=resolved.name,
        registered_at=datetime.now(),
    )
    registry.projects.append(entry)
    _save_registry(registry, registry_path)
    log.info("register_project", name=resolved.name, path=str(resolved))


def update_project_stats(
    project_path: Path,
    experiment_count: int | None = None,
    latest_score: float | None = None,
    registry_path: Path | None = None,
) -> None:
    """Update a registered project's stats after an experiment completes."""
    resolved = project_path.resolve()
    registry = _load_registry(registry_path)

    for entry in registry.projects:
        if entry.path == str(resolved):
            entry.last_experiment_at = datetime.now()
            if experiment_count is not None:
                entry.experiment_count = experiment_count
            if latest_score is not None:
                entry.latest_score = latest_score
            _save_registry(registry, registry_path)
            log.info(
                "update_project_stats",
                name=entry.name,
                experiments=entry.experiment_count,
                score=entry.latest_score,
            )
            return

    log.warning("update_project_stats_not_found", path=str(resolved))


def get_project_paths(registry_path: Path | None = None) -> list[Path]:
    """Return all registered project paths that still exist on disk."""
    registry = _load_registry(registry_path)
    paths: list[Path] = []
    for entry in registry.projects:
        p = Path(entry.path)
        if p.is_dir():
            paths.append(p)
        else:
            log.debug("registry_stale_entry", path=entry.path)
    return paths


def list_projects(registry_path: Path | None = None) -> list[ProjectEntry]:
    """Return all registered project entries."""
    registry = _load_registry(registry_path)
    return registry.projects


def populate_from_directory(projects_dir: Path, registry_path: Path | None = None) -> int:
    """Auto-populate registry by scanning a directory for .factory/results.tsv.

    Used as migration path from discover_projects() to the registry.
    Returns the number of newly registered projects.
    """
    from factory.insights import discover_projects

    existing = _load_registry(registry_path)
    existing_paths = {e.path for e in existing.projects}

    discovered = discover_projects(projects_dir)
    added = 0
    for path in discovered:
        resolved = str(path.resolve())
        if resolved not in existing_paths:
            register_project(path, registry_path)
            added += 1

    if added:
        log.info("registry_populated", added=added, dir=str(projects_dir))
    return added
