"""Run metadata persistence for factory session recovery.

Stores per-run metadata in .factory/runs/<run_id>.json to enable
session listing, resumption, and reconstruction.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict

log = structlog.get_logger()

_RUN_ID_RE = re.compile(r"^[a-f0-9]{8}$")


def _validate_run_id(run_id: str) -> None:
    if not _RUN_ID_RE.match(run_id):
        raise ValueError(f"Invalid run_id: {run_id!r} (must match ^[a-f0-9]{{8}}$)")


class SessionRunStatus(str, Enum):
    """Lifecycle status of a factory run."""

    running = "running"
    completed = "completed"
    error = "error"


class RunMetadata(BaseModel):
    """Metadata for a single factory CEO run, persisted as JSON."""

    model_config = ConfigDict(strict=True, extra="forbid")

    run_id: str
    branch: str
    worktree_path: str
    base_branch: str
    status: SessionRunStatus
    claude_session_id: str | None = None
    child_session_ids: list[str] = []
    created_at: str
    completed_at: str | None = None
    mode: str | None = None
    experiment_ids: list[int] = []


def _runs_dir(project_path: Path) -> Path:
    return project_path / ".factory" / "runs"


def save_run(project_path: Path, metadata: RunMetadata) -> None:
    """Write run metadata to .factory/runs/{run_id}.json and emit run.created event."""
    _validate_run_id(metadata.run_id)
    runs = _runs_dir(project_path)
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"{metadata.run_id}.json"
    path.write_text(metadata.model_dump_json(indent=2) + "\n")
    log.info("run_saved", run_id=metadata.run_id, status=metadata.status.value)

    try:
        from factory.events import emit_event
        emit_event(project_path, "run.created", data={
            "run_id": metadata.run_id,
            "branch": metadata.branch,
            "status": metadata.status.value,
        })
    except Exception:
        pass


def load_run(project_path: Path, run_id: str) -> RunMetadata | None:
    """Load run metadata by ID. Returns None if not found."""
    _validate_run_id(run_id)
    path = _runs_dir(project_path) / f"{run_id}.json"
    if not path.exists():
        return None
    return RunMetadata.model_validate_json(path.read_text())


def list_runs(project_path: Path) -> list[RunMetadata]:
    """Load all run metadata files from .factory/runs/."""
    runs = _runs_dir(project_path)
    if not runs.is_dir():
        return []
    results: list[RunMetadata] = []
    for f in sorted(runs.iterdir()):
        if f.suffix != ".json":
            continue
        try:
            results.append(RunMetadata.model_validate_json(f.read_text()))
        except Exception:
            log.warning("run_load_failed", path=str(f))
    return results


def update_run(project_path: Path, run_id: str, **kwargs: object) -> RunMetadata | None:
    """Partial update of run metadata. Returns updated metadata, or None if not found."""
    _validate_run_id(run_id)
    meta = load_run(project_path, run_id)
    if meta is None:
        return None

    data = meta.model_dump()
    data.update(kwargs)
    updated = RunMetadata.model_validate(data)

    path = _runs_dir(project_path) / f"{run_id}.json"
    path.write_text(updated.model_dump_json(indent=2) + "\n")
    log.info("run_updated", run_id=run_id, **{k: str(v) for k, v in kwargs.items()})

    try:
        from factory.events import emit_event
        emit_event(project_path, "run.updated", data={
            "run_id": run_id,
            **{k: str(v) for k, v in kwargs.items()},
        })
    except Exception:
        pass

    return updated


def delete_run(project_path: Path, run_id: str) -> bool:
    """Delete run metadata file. Returns True if deleted, False if not found."""
    _validate_run_id(run_id)
    path = _runs_dir(project_path) / f"{run_id}.json"
    if not path.exists():
        return False
    path.unlink()
    log.info("run_deleted", run_id=run_id)
    return True


def prune_runs(
    project_path: Path,
    older_than_days: int = 30,
    *,
    dry_run: bool = False,
    prune_all: bool = False,
) -> list[str]:
    """Delete run metadata and branches older than N days (or all completed runs).

    Returns list of human-readable descriptions of pruned items.
    """
    runs = list_runs(project_path)
    now = datetime.now(timezone.utc)
    pruned: list[str] = []

    for meta in runs:
        if meta.status == SessionRunStatus.running and not prune_all:
            continue

        created = datetime.fromisoformat(meta.created_at)
        age_days = (now - created).days

        if not prune_all and age_days < older_than_days:
            continue

        desc = f"run {meta.run_id} (branch={meta.branch}, age={age_days}d, status={meta.status.value})"

        if dry_run:
            pruned.append(f"Would prune: {desc}")
            continue

        delete_run(project_path, meta.run_id)

        subprocess.run(
            ["git", "branch", "-D", meta.branch],
            cwd=project_path,
            capture_output=True,
        )
        pruned.append(f"Pruned: {desc}")
        log.info("run_pruned", run_id=meta.run_id, branch=meta.branch)

    return pruned
