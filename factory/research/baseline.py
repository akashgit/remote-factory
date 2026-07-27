"""Baseline management — track, invalidate, and promote research baselines."""

from __future__ import annotations

import fnmatch
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import structlog
from filelock import FileLock

from factory.models import BaselineRecord, BaselineState

log = structlog.get_logger()

_BASELINE_FILE = "baseline.json"
_LOCK_FILE = ".baseline.lock"


def _research_dir(project_path: Path) -> Path:
    return project_path / ".factory" / "research"


def _baseline_path(project_path: Path) -> Path:
    return _research_dir(project_path) / _BASELINE_FILE


def _lock_path(project_path: Path) -> Path:
    return _research_dir(project_path) / _LOCK_FILE


def load_baseline(project_path: Path) -> BaselineRecord | None:
    """Load baseline from .factory/research/baseline.json."""
    path = _baseline_path(project_path)
    if not path.exists():
        log.debug("baseline.not_found", path=str(path))
        return None
    try:
        data = json.loads(path.read_text())
        return BaselineRecord.model_validate(data)
    except (json.JSONDecodeError, Exception) as exc:
        log.warning("baseline.corrupt", path=str(path), error=str(exc))
        return None


def save_baseline(project_path: Path, record: BaselineRecord) -> None:
    """Persist baseline to disk with file lock."""
    research = _research_dir(project_path)
    research.mkdir(parents=True, exist_ok=True)
    with FileLock(_lock_path(project_path)):
        _baseline_path(project_path).write_text(
            record.model_dump_json(indent=2)
        )
    log.info("baseline.saved", commit=record.current.commit_sha)


def create_baseline(
    project_path: Path,
    *,
    commit_sha: str,
    metric_value: float,
    metric: str,
    run_id: str,
) -> BaselineRecord:
    """Create a new baseline from a fresh eval run."""
    state = BaselineState(
        commit_sha=commit_sha,
        metric_value=metric_value,
        metric=metric,
        run_id=run_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        source="run",
    )
    existing = load_baseline(project_path)
    history: list[BaselineState] = []
    if existing:
        history = [existing.current, *existing.history]
    record = BaselineRecord(current=state, history=history)
    save_baseline(project_path, record)
    return record


# ── staleness detection ────────────────────────────────────────


def _head_sha(project_path: Path, branch: str = "main") -> str | None:
    """Get the HEAD SHA of a branch."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", branch],
            cwd=project_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _changed_files_since(
    project_path: Path, base_sha: str, head_sha: str
) -> list[str]:
    """Get files changed between two commits."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_sha}..{head_sha}"],
            cwd=project_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return [f for f in result.stdout.strip().splitlines() if f]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def _files_overlap_surfaces(
    changed_files: list[str], surface_patterns: list[str]
) -> list[str]:
    """Return changed files that match any surface pattern (glob)."""
    hits: list[str] = []
    for f in changed_files:
        for pattern in surface_patterns:
            if fnmatch.fnmatch(f, pattern):
                hits.append(f)
                break
    return hits


class StalenessResult:
    """Result of a baseline staleness check."""

    __slots__ = ("stale", "reason", "changed_files", "head_sha")

    def __init__(
        self,
        stale: bool,
        reason: str,
        changed_files: list[str] | None = None,
        head_sha: str | None = None,
    ) -> None:
        self.stale = stale
        self.reason = reason
        self.changed_files = changed_files or []
        self.head_sha = head_sha

    def __repr__(self) -> str:
        return f"StalenessResult(stale={self.stale}, reason={self.reason!r})"


def check_staleness(
    project_path: Path,
    target_branch: str = "main",
    mutable_surfaces: list[str] | None = None,
) -> StalenessResult:
    """Two-tier baseline invalidation check.

    Returns a StalenessResult with:
    - stale=False, reason="current" — HEAD matches baseline commit
    - stale=False, reason="fast_forward" — HEAD advanced but no impactful changes
    - stale=True, reason="no_baseline" — no baseline exists
    - stale=True, reason="impact_detected" — changed files overlap surfaces
    - stale=True, reason="no_git" — can't read git state
    """
    baseline = load_baseline(project_path)
    if baseline is None:
        return StalenessResult(stale=True, reason="no_baseline")

    head = _head_sha(project_path, target_branch)
    if head is None:
        return StalenessResult(stale=True, reason="no_git")

    if head == baseline.current.commit_sha:
        return StalenessResult(stale=False, reason="current", head_sha=head)

    changed = _changed_files_since(
        project_path, baseline.current.commit_sha, head
    )
    if not changed:
        return StalenessResult(
            stale=False, reason="fast_forward", changed_files=[], head_sha=head
        )

    surfaces = mutable_surfaces or []
    overlapping = _files_overlap_surfaces(changed, surfaces)

    if overlapping:
        log.info(
            "baseline.impact_detected",
            overlapping=overlapping,
            total_changed=len(changed),
        )
        return StalenessResult(
            stale=True,
            reason="impact_detected",
            changed_files=overlapping,
            head_sha=head,
        )

    return StalenessResult(
        stale=False, reason="fast_forward", changed_files=changed, head_sha=head
    )


def fast_forward(project_path: Path, new_commit: str) -> BaselineRecord:
    """Update baseline commit SHA without re-running. Preserves metric."""
    record = load_baseline(project_path)
    if record is None:
        raise ValueError("No baseline to fast-forward")

    old = record.current
    record.current = BaselineState(
        commit_sha=new_commit,
        metric_value=old.metric_value,
        metric=old.metric,
        run_id=old.run_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        source=old.source,
        source_experiment_id=old.source_experiment_id,
    )
    record.history = [old, *record.history]
    save_baseline(project_path, record)
    log.info(
        "baseline.fast_forwarded",
        old_commit=old.commit_sha[:8],
        new_commit=new_commit[:8],
    )
    return record


def promote_experiment(
    project_path: Path,
    *,
    metric_value: float,
    metric: str,
    commit_sha: str,
    experiment_id: int,
    run_id: str,
) -> BaselineRecord:
    """Copy a hypothesis experiment's metric as the new baseline."""
    state = BaselineState(
        commit_sha=commit_sha,
        metric_value=metric_value,
        metric=metric,
        run_id=run_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        source="promoted",
        source_experiment_id=experiment_id,
    )
    existing = load_baseline(project_path)
    history: list[BaselineState] = []
    if existing:
        history = [existing.current, *existing.history]
    record = BaselineRecord(current=state, history=history)
    save_baseline(project_path, record)
    log.info(
        "baseline.promoted",
        experiment_id=experiment_id,
        metric_value=metric_value,
    )
    return record


# ── analysis gating ────────────────────────────────────────────


def needs_reanalysis(project_path: Path) -> bool:
    """Check if R1/R1.5 analysis needs to re-run.

    Returns True if:
    - No baseline exists
    - analysis_commit is None (never analyzed)
    - Baseline metric changed since last analysis
    """
    record = load_baseline(project_path)
    if record is None:
        return True
    if record.analysis_commit is None:
        return True
    if record.analysis_metric is None:
        return True
    return record.current.metric_value != record.analysis_metric


def mark_analysis_done(project_path: Path) -> None:
    """Tag analysis with current baseline commit and metric."""
    record = load_baseline(project_path)
    if record is None:
        log.warning("baseline.mark_analysis_no_baseline")
        return
    record.analysis_commit = record.current.commit_sha
    record.analysis_metric = record.current.metric_value
    save_baseline(project_path, record)
    log.info("baseline.analysis_marked", commit=record.analysis_commit)
