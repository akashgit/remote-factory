"""FastAPI dashboard server — serves UI and SSE event stream."""

from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

_STATIC_DIR = Path(__file__).parent / "static"


def create_app(projects_dir: Path) -> FastAPI:
    """Create the FastAPI dashboard app bound to a projects directory."""
    app = FastAPI(title="Factory Dashboard")

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((_STATIC_DIR / "index.html").read_text())

    @app.get("/api/projects")
    async def list_projects() -> list[dict[str, Any]]:
        from factory.events import discover_factory_projects

        projects: list[dict[str, Any]] = []
        for path in discover_factory_projects(projects_dir):
            info = _project_summary(path)
            projects.append(info)
        return projects

    @app.get("/api/projects/{name}/history")
    async def project_history(name: str) -> list[dict[str, Any]]:
        path = projects_dir / name
        if not (path / ".factory" / "results.tsv").exists():
            return []
        return _load_tsv(path / ".factory" / "results.tsv")

    @app.get("/api/projects/{name}/events")
    async def project_events(name: str, limit: int = 100) -> list[dict[str, Any]]:
        from factory.events import load_events

        path = projects_dir / name
        events = load_events(path)
        return events[-limit:]

    @app.get("/api/events/stream")
    async def event_stream(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _sse_generator(projects_dir, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


async def _sse_generator(projects_dir: Path, request: Request):
    """Tail all events.jsonl files and yield new events as SSE."""
    from factory.events import discover_factory_projects

    # Track file positions to only read new lines
    positions: dict[str, int] = {}

    while True:
        if await request.is_disconnected():
            break

        for project in discover_factory_projects(projects_dir):
            events_file = project / ".factory" / "events.jsonl"
            if not events_file.exists():
                continue

            key = str(events_file)
            pos = positions.get(key, 0)
            try:
                file_size = events_file.stat().st_size
            except OSError:
                continue

            if file_size > pos:
                with open(events_file) as f:
                    f.seek(pos)
                    for line in f:
                        stripped = line.strip()
                        if stripped:
                            yield f"data: {stripped}\n\n"
                    positions[key] = f.tell()

        await asyncio.sleep(1)


def _project_summary(path: Path) -> dict[str, Any]:
    """Build a summary dict for a single project."""
    info: dict[str, Any] = {
        "name": path.name,
        "path": str(path),
        "has_config": (path / ".factory" / "config.json").exists(),
        "experiment_count": 0,
        "keep_count": 0,
        "revert_count": 0,
        "latest_score": None,
        "last_experiment": None,
        "goal": None,
        "active": False,
    }

    # Read config
    config_path = path / ".factory" / "config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            info["goal"] = config.get("goal", "")
        except (json.JSONDecodeError, OSError):
            pass

    # Read experiment history
    tsv_path = path / ".factory" / "results.tsv"
    if tsv_path.exists():
        rows = _load_tsv(tsv_path)
        info["experiment_count"] = len(rows)
        info["keep_count"] = sum(1 for r in rows if r.get("verdict") == "keep")
        info["revert_count"] = sum(1 for r in rows if r.get("verdict") == "revert")

        scores = [float(r["score_after"]) for r in rows if r.get("score_after")]
        if scores:
            info["latest_score"] = scores[-1]

        if rows:
            last = rows[-1]
            info["last_experiment"] = {
                "id": last.get("id"),
                "hypothesis": last.get("hypothesis", "")[:80],
                "verdict": last.get("verdict"),
                "delta": last.get("delta"),
                "timestamp": last.get("timestamp"),
            }

    # Check if actively running (events in last 5 minutes)
    events_file = path / ".factory" / "events.jsonl"
    if events_file.exists():
        try:
            lines = events_file.read_text().strip().splitlines()
            if lines:
                last_event = json.loads(lines[-1])
                last_ts = datetime.fromisoformat(last_event["timestamp"])
                delta = (datetime.now(last_ts.tzinfo) - last_ts).total_seconds()
                info["active"] = delta < 300  # Active if event in last 5 min
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    return info


def _load_tsv(path: Path) -> list[dict[str, str]]:
    """Load a TSV file into a list of dicts."""
    with open(path, newline="") as f:
        reader = csv.DictReader(f, dialect="excel-tab")
        return list(reader)
