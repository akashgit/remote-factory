"""Bob usage tracking — log and ceiling enforcement."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

import structlog

log = structlog.get_logger()

USAGE_LOG_NAME = "bob_usage.jsonl"


class UsageEntry(TypedDict):
    timestamp: str
    role: str
    cwd: str
    duration_seconds: float
    exit_code: int
    dry_run: bool


def get_usage_log_path(project_path: Path) -> Path:
    """Return the path to the bob usage log for a project."""
    return project_path / ".factory" / USAGE_LOG_NAME


def log_usage(
    project_path: Path,
    role: str,
    cwd: Path,
    duration_seconds: float,
    exit_code: int,
    dry_run: bool = False,
) -> None:
    """Append a usage entry to the project's bob_usage.jsonl."""
    log_path = get_usage_log_path(project_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry: UsageEntry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "cwd": str(cwd),
        "duration_seconds": duration_seconds,
        "exit_code": exit_code,
        "dry_run": dry_run,
    }

    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def count_cycle_invocations(project_path: Path, cycle_start: datetime | None = None) -> int:
    """Count non-dry-run bob invocations in the current cycle.

    If cycle_start is None, returns 0 (no cycle tracking without explicit start).
    """
    if cycle_start is None:
        return 0

    log_path = get_usage_log_path(project_path)
    if not log_path.exists():
        return 0

    count = 0
    cycle_start_iso = cycle_start.isoformat()

    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts = entry.get("timestamp", "")
                if ts >= cycle_start_iso and not entry.get("dry_run", False):
                    count += 1
            except json.JSONDecodeError:
                continue

    return count


def get_cycle_ceiling() -> int:
    """Get the per-cycle invocation ceiling from env var."""
    from factory.user_config import resolve

    return int(resolve("bob_max_invocations_per_cycle", env_var="FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE", default="8") or "8")


class CeilingExceededError(Exception):
    """Raised when a bob invocation ceiling is exceeded."""

    def __init__(self, ceiling_name: str, current: int, limit: int, env_var: str) -> None:
        self.ceiling_name = ceiling_name
        self.current = current
        self.limit = limit
        self.env_var = env_var
        super().__init__(
            f"Bob {ceiling_name} ceiling exceeded: {current}/{limit}. "
            f"To increase, set {env_var}={limit + 5}"
        )


@dataclass
class CeilingWarning:
    """Warning when approaching a ceiling (≤2 invocations remaining)."""

    ceiling_name: str
    remaining: int
    limit: int


def _emit_warning_event(project_path: Path, warning: CeilingWarning) -> None:
    """Emit a warning event to .factory/events.jsonl."""
    try:
        from factory.events import emit_event

        emit_event(
            project_path,
            "bob.ceiling_warning",
            data={
                "ceiling": warning.ceiling_name,
                "remaining": warning.remaining,
                "limit": warning.limit,
            },
        )
    except Exception:
        log.warning("Failed to emit ceiling warning event", exc_info=True)


def check_ceilings(
    project_path: Path,
    cycle_start: datetime | None = None,
) -> CeilingWarning | None:
    """Check per-cycle ceiling before a bob invocation.

    Raises CeilingExceededError if the per-cycle ceiling is exceeded.
    Returns CeilingWarning if ≤2 invocations remain before the ceiling.
    """
    # Check per-cycle ceiling
    cycle_count = count_cycle_invocations(project_path, cycle_start)
    cycle_limit = get_cycle_ceiling()
    if cycle_count >= cycle_limit:
        raise CeilingExceededError(
            "per-cycle", cycle_count, cycle_limit, "FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE"
        )

    # Check for approaching ceiling (≤2 remaining)
    remaining = cycle_limit - cycle_count
    if remaining <= 2:
        warning = CeilingWarning("per-cycle", remaining, cycle_limit)
        log.warning(
            "bob_ceiling_approaching",
            ceiling=warning.ceiling_name,
            remaining=warning.remaining,
            limit=warning.limit,
        )
        _emit_warning_event(project_path, warning)
        return warning

    return None
