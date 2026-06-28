"""Run index — tracks factory session metadata in .factory/runs/<run-id>.json."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict

log = structlog.get_logger()


class RunMetadata(BaseModel):
    """Metadata for a single factory run / session."""

    model_config = ConfigDict(strict=True, extra="forbid")

    run_id: str
    branch: str
    worktree_path: str
    created_at: str
    mode: str
    status: Literal["active", "completed", "crashed"]


def _runs_dir(project_path: Path) -> Path:
    return project_path / ".factory" / "runs"


def write_run(project_path: Path, metadata: RunMetadata) -> None:
    """Write run metadata atomically to .factory/runs/<run-id>.json."""
    runs = _runs_dir(project_path)
    runs.mkdir(parents=True, exist_ok=True)
    target = runs / f"{metadata.run_id}.json"
    data = json.dumps(metadata.model_dump(), indent=2) + "\n"
    tmp_fd, tmp_path = tempfile.mkstemp(dir=runs, suffix=".tmp")
    try:
        with open(tmp_fd, "w") as f:
            f.write(data)
        Path(tmp_path).replace(target)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    log.debug("run_index.write", run_id=metadata.run_id, status=metadata.status)


def read_run(project_path: Path, run_id: str) -> RunMetadata | None:
    """Load one run's metadata, or None if not found."""
    path = _runs_dir(project_path) / f"{run_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return RunMetadata.model_validate(data)


def list_runs(project_path: Path) -> list[RunMetadata]:
    """List all runs sorted by created_at descending."""
    runs = _runs_dir(project_path)
    if not runs.is_dir():
        return []
    results: list[RunMetadata] = []
    for p in runs.glob("*.json"):
        try:
            data = json.loads(p.read_text())
            results.append(RunMetadata.model_validate(data))
        except Exception:
            log.warning("run_index.skip_corrupt", path=str(p))
    results.sort(key=lambda r: r.created_at, reverse=True)
    return results


def update_status(
    project_path: Path,
    run_id: str,
    status: Literal["active", "completed", "crashed"],
) -> bool:
    """Update the status field of an existing run. Returns True if updated."""
    meta = read_run(project_path, run_id)
    if meta is None:
        return False
    meta = meta.model_copy(update={"status": status})
    write_run(project_path, meta)
    log.debug("run_index.update_status", run_id=run_id, status=status)
    return True


def delete_run(project_path: Path, run_id: str) -> bool:
    """Delete a run metadata file. Returns True if deleted."""
    path = _runs_dir(project_path) / f"{run_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True
