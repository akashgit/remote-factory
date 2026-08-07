"""Runner usage tracking — log and ceiling enforcement.

Generalized for any runner (Bob, OpenCode, etc.) via runner_name parameter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

import structlog

log = structlog.get_logger()


class UsageEntry(TypedDict):
    timestamp: str
    role: str
    cwd: str
    duration_seconds: float
    exit_code: int
    dry_run: bool


def get_usage_log_path(project_path: Path, runner_name: str = "bob") -> Path:
    """Return the path to the usage log for a project."""
    return project_path / ".factory" / f"{runner_name}_usage.jsonl"


def log_usage(
    project_path: Path,
    role: str,
    cwd: Path,
    duration_seconds: float,
    exit_code: int,
    dry_run: bool = False,
    runner_name: str = "bob",
) -> None:
    """Append a usage entry to the project's usage log."""
    log_path = get_usage_log_path(project_path, runner_name)
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


def count_cycle_invocations(
    project_path: Path,
    cycle_start: datetime | None = None,
    runner_name: str = "bob",
) -> int:
    """Count non-dry-run invocations in the current cycle.

    If cycle_start is None, returns 0 (no cycle tracking without explicit start).
    """
    if cycle_start is None:
        return 0

    log_path = get_usage_log_path(project_path, runner_name)
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


def get_cycle_ceiling(runner_name: str = "bob") -> int:
    """Get the per-cycle invocation ceiling from env var."""
    from factory.user_config import resolve

    upper = runner_name.upper()
    env_var = f"FACTORY_{upper}_MAX_INVOCATIONS_PER_CYCLE"
    config_key = f"{runner_name}_max_invocations_per_cycle"
    return int(resolve(config_key, env_var=env_var, default="8") or "8")


class CeilingExceededError(Exception):
    """Raised when a runner invocation ceiling is exceeded."""

    def __init__(self, ceiling_name: str, current: int, limit: int, env_var: str, runner_name: str = "bob") -> None:
        self.ceiling_name = ceiling_name
        self.current = current
        self.limit = limit
        self.env_var = env_var
        self.runner_name = runner_name
        display = runner_name.capitalize()
        super().__init__(
            f"{display} {ceiling_name} ceiling exceeded: {current}/{limit}. "
            f"To increase, set {env_var}={limit + 5}"
        )


@dataclass
class CeilingWarning:
    """Warning when approaching a ceiling (≤2 invocations remaining)."""

    ceiling_name: str
    remaining: int
    limit: int


def _emit_warning_event(project_path: Path, warning: CeilingWarning, runner_name: str = "bob") -> None:
    """Emit a warning event to .factory/events.jsonl."""
    try:
        from factory.events import emit_event

        emit_event(
            project_path,
            f"{runner_name}.ceiling_warning",
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
    runner_name: str = "bob",
) -> CeilingWarning | None:
    """Check per-cycle ceiling before a runner invocation.

    Raises CeilingExceededError if the per-cycle ceiling is exceeded.
    Returns CeilingWarning if ≤2 invocations remain before the ceiling.
    """
    upper = runner_name.upper()
    env_var = f"FACTORY_{upper}_MAX_INVOCATIONS_PER_CYCLE"

    cycle_count = count_cycle_invocations(project_path, cycle_start, runner_name)
    cycle_limit = get_cycle_ceiling(runner_name)
    if cycle_count >= cycle_limit:
        raise CeilingExceededError(
            "per-cycle", cycle_count, cycle_limit, env_var, runner_name
        )

    remaining = cycle_limit - cycle_count
    if remaining <= 2:
        warning = CeilingWarning("per-cycle", remaining, cycle_limit)
        log.warning(
            f"{runner_name}_ceiling_approaching",
            ceiling=warning.ceiling_name,
            remaining=warning.remaining,
            limit=warning.limit,
        )
        _emit_warning_event(project_path, warning, runner_name)
        return warning

    return None
