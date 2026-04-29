"""Bob usage tracking — log and ceiling enforcement."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

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


def count_today_invocations(project_path: Path, *, include_dry_run: bool = False) -> int:
    """Count non-dry-run bob invocations from today."""
    log_path = get_usage_log_path(project_path)
    if not log_path.exists():
        return 0

    today = datetime.now(timezone.utc).date().isoformat()
    count = 0

    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("timestamp", "").startswith(today):
                    if include_dry_run or not entry.get("dry_run", False):
                        count += 1
            except json.JSONDecodeError:
                continue

    return count


def count_cycle_invocations(project_path: Path, cycle_start: datetime | None = None) -> int:
    """Count non-dry-run bob invocations in the current cycle.

    If cycle_start is None, counts all invocations from today (approximation).
    """
    if cycle_start is None:
        return count_today_invocations(project_path)

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
    return int(os.environ.get("FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE", "3"))


def get_daily_ceiling() -> int:
    """Get the per-day invocation ceiling from env var."""
    return int(os.environ.get("FACTORY_BOB_MAX_INVOCATIONS_PER_DAY", "20"))


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


def check_ceilings(
    project_path: Path,
    cycle_start: datetime | None = None,
) -> None:
    """Check all ceilings before a bob invocation.

    Raises CeilingExceededError if any ceiling is exceeded.
    """
    # Check per-day ceiling
    daily_count = count_today_invocations(project_path)
    daily_limit = get_daily_ceiling()
    if daily_count >= daily_limit:
        raise CeilingExceededError(
            "daily", daily_count, daily_limit, "FACTORY_BOB_MAX_INVOCATIONS_PER_DAY"
        )

    # Check per-cycle ceiling
    cycle_count = count_cycle_invocations(project_path, cycle_start)
    cycle_limit = get_cycle_ceiling()
    if cycle_count >= cycle_limit:
        raise CeilingExceededError(
            "per-cycle", cycle_count, cycle_limit, "FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE"
        )
