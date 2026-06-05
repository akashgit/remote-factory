"""Event system for factory observability — append-only JSONL event log."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()


def emit_event(
    project_path: Path,
    event_type: str,
    *,
    agent: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a structured event to .factory/events.jsonl. Returns the event dict."""
    project_path = project_path.resolve()
    event = {
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project": project_path.name,
        "agent": agent,
        "data": data or {},
    }

    from factory.store import ensure_factory_dir
    events_dir = project_path / ".factory"
    ensure_factory_dir(events_dir)
    events_file = events_dir / "events.jsonl"

    with open(events_file, "a") as f:
        f.write(json.dumps(event) + "\n")

    log.debug("event_emitted", type=event_type, project=project_path.name, agent=agent)
    return event


def load_events(
    project_path: Path,
    *,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Load events from .factory/events.jsonl, optionally filtered by timestamp."""
    project_path = project_path.resolve()
    events_file = project_path / ".factory" / "events.jsonl"
    if not events_file.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in events_file.read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if since:
            event_ts = datetime.fromisoformat(event["timestamp"])
            if event_ts <= since:
                continue
        events.append(event)

    return events


def sum_agent_costs(
    project_path: Path,
    *,
    since: datetime | None = None,
) -> float:
    """Sum total_cost_usd from agent.completed events, optionally since a timestamp."""
    events = load_events(project_path, since=since)
    total = 0.0
    for ev in events:
        if ev.get("type") != "agent.completed":
            continue
        total += ev.get("data", {}).get("total_cost_usd", 0.0)
    return total


def discover_factory_projects(projects_dir: Path, *, max_depth: int = 3) -> list[Path]:
    """Find all subdirectories with .factory/ directories, up to max_depth levels."""
    if not projects_dir.exists():
        return []
    results: list[Path] = []

    def _scan(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(directory.iterdir())
        except PermissionError:
            return
        for child in children:
            if not child.is_dir() or child.name.startswith("."):
                continue
            if (child / ".factory").is_dir():
                results.append(child)
            # Always recurse into children — a parent with .factory/
            # may contain nested projects with their own .factory/
            _scan(child, depth + 1)

    _scan(projects_dir, 1)
    return sorted(results)
