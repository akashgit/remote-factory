"""Research directory management — creates and manages .factory/research/ structure."""

from __future__ import annotations

import json
from pathlib import Path

import structlog

log = structlog.get_logger()


def ensure_research_dir(project_path: Path) -> Path:
    """Create ``.factory/research/runs/`` if needed and return the research dir."""
    research_dir = project_path / ".factory" / "research"
    runs_dir = research_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    log.debug("research_dir_ensured", path=str(research_dir))
    return research_dir


def create_run_dir(project_path: Path, cycle_id: str) -> Path:
    """Create and return ``.factory/research/runs/<cycle_id>/``."""
    run_dir = project_path / ".factory" / "research" / "runs" / cycle_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log.debug("run_dir_created", path=str(run_dir), cycle_id=cycle_id)
    return run_dir


def save_run_summary(run_dir: Path, summary: dict) -> None:
    """Write ``summary.json`` to the given run directory."""
    path = run_dir / "summary.json"
    path.write_text(json.dumps(summary, indent=2, default=str))
    log.debug("run_summary_saved", path=str(path))


def load_run_summary(run_dir: Path) -> dict | None:
    """Load ``summary.json`` from the given run directory, or return None."""
    path = run_dir / "summary.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def list_runs(project_path: Path) -> list[Path]:
    """List all run directories sorted by name."""
    runs_dir = project_path / ".factory" / "research" / "runs"
    if not runs_dir.exists():
        return []
    return sorted(p for p in runs_dir.iterdir() if p.is_dir())


def write_comparison(
    project_path: Path, current_id: str, previous_id: str, comparison: str
) -> None:
    """Write a comparison report between two runs."""
    research_dir = ensure_research_dir(project_path)
    path = research_dir / f"comparison_{previous_id}_vs_{current_id}.md"
    path.write_text(comparison)
    log.debug(
        "comparison_written",
        path=str(path),
        current=current_id,
        previous=previous_id,
    )
