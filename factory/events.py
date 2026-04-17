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
    event = {
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project": project_path.name,
        "agent": agent,
        "data": data or {},
    }

    events_dir = project_path / ".factory"
    events_dir.mkdir(parents=True, exist_ok=True)
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


def discover_factory_projects(projects_dir: Path) -> list[Path]:
    """Find all subdirectories with .factory/ directories."""
    if not projects_dir.exists():
        return []
    return sorted(
        child
        for child in projects_dir.iterdir()
        if child.is_dir() and (child / ".factory").is_dir()
    )
