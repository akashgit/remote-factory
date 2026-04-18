"""FastAPI dashboard server — serves UI and SSE event stream."""

from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

log = structlog.get_logger()

_STATIC_DIR = Path(__file__).parent / "static"


def create_app(projects_dir: Path) -> FastAPI:
    """Create the FastAPI dashboard app bound to a projects directory."""
    log.info("dashboard_create_app", projects_dir=str(projects_dir))
    app = FastAPI(title="Factory Dashboard")

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        log.info("dashboard_request", endpoint="/")
        return HTMLResponse((_STATIC_DIR / "index.html").read_text())

    @app.get("/api/projects")
    async def list_projects() -> list[dict[str, Any]]:
        log.info("dashboard_request", endpoint="/api/projects")
        from factory.events import discover_factory_projects

        projects: list[dict[str, Any]] = []
        for path in discover_factory_projects(projects_dir):
            info = _project_summary(path)
            projects.append(info)
        log.debug("dashboard_list_projects", project_count=len(projects))
        return projects

    @app.get("/api/projects/{name}/history")
    async def project_history(name: str) -> list[dict[str, Any]]:
        log.info("dashboard_request", endpoint="/api/projects/{name}/history", project=name)
        path = projects_dir / name
        if not (path / ".factory" / "results.tsv").exists():
            return []
        rows = _load_tsv(path / ".factory" / "results.tsv")
        for row in rows:
            row["dimensions"] = _load_experiment_dimensions(path, row.get("id", ""))
        return rows

    @app.get("/api/projects/{name}/dimensions")
    async def project_dimensions(name: str) -> dict[str, Any]:
        log.info(
            "dashboard_request",
            endpoint="/api/projects/{name}/dimensions",
            project=name,
        )
        path = projects_dir / name
        dims = _load_latest_dimensions(path)
        return {"dimensions": dims}

    @app.get("/api/projects/{name}/events")
    async def project_events(name: str, limit: int = 100) -> list[dict[str, Any]]:
        log.info("dashboard_request", endpoint="/api/projects/{name}/events", project=name, limit=limit)
        from factory.events import load_events

        path = projects_dir / name
        events = load_events(path)
        return events[-limit:]

    @app.get("/api/summary")
    async def summary() -> dict[str, Any]:
        log.info("dashboard_request", endpoint="/api/summary")
        from factory.events import discover_factory_projects

        total_projects = 0
        active_projects = 0
        total_experiments = 0
        keep_count = 0
        revert_count = 0
        score_sum = 0.0
        score_count = 0

        for path in discover_factory_projects(projects_dir):
            info = _project_summary(path)
            total_projects += 1
            if info.get("active"):
                active_projects += 1
            total_experiments += info.get("experiment_count", 0)
            keep_count += info.get("keep_count", 0)
            revert_count += info.get("revert_count", 0)
            if info.get("latest_score") is not None:
                score_sum += info["latest_score"]
                score_count += 1

        return {
            "total_projects": total_projects,
            "active_projects": active_projects,
            "avg_score": score_sum / score_count if score_count > 0 else None,
            "total_experiments": total_experiments,
            "keep_count": keep_count,
            "revert_count": revert_count,
            "keep_rate": keep_count / total_experiments if total_experiments > 0 else 0,
        }

    @app.get("/api/events/stream")
    async def event_stream(request: Request) -> StreamingResponse:
        log.info("dashboard_request", endpoint="/api/events/stream")
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

    log.info("sse_client_connected", projects_dir=str(projects_dir))

    # Track file positions to only read new lines
    positions: dict[str, int] = {}

    while True:
        if await request.is_disconnected():
            log.info("sse_client_disconnected", projects_dir=str(projects_dir))
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
    log.debug("project_summary_start", project=path.name)
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
        info["scores"] = scores
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

    log.debug(
        "project_summary_complete",
        project=path.name,
        experiment_count=info["experiment_count"],
        active=info["active"],
    )
    return info


def _load_tsv(path: Path) -> list[dict[str, str]]:
    """Load a TSV file into a list of dicts."""
    log.debug("load_tsv", path=str(path))
    with open(path, newline="") as f:
        reader = csv.DictReader(f, dialect="excel-tab")
        rows = list(reader)
    log.debug("load_tsv_complete", path=str(path), row_count=len(rows))
    return rows


def _load_experiment_dimensions(
    project_path: Path, exp_id: str
) -> list[dict[str, Any]]:
    """Load dimension scores from an experiment's eval_after.json."""
    if not exp_id:
        return []
    exp_dir = project_path / ".factory" / "experiments" / str(exp_id).zfill(3)
    eval_file = exp_dir / "eval_after.json"
    if not eval_file.exists():
        return []
    try:
        data = json.loads(eval_file.read_text())
        results = data.get("results", [])
        return [
            {
                "name": r.get("name", ""),
                "score": r.get("score", 0.0),
                "weight": r.get("weight", 0.0),
                "passed": r.get("passed", False),
            }
            for r in results
        ]
    except (json.JSONDecodeError, OSError):
        return []


def _load_latest_dimensions(project_path: Path) -> list[dict[str, Any]]:
    """Load dimensions from the most recent experiment's eval_after.json."""
    exp_base = project_path / ".factory" / "experiments"
    if not exp_base.exists():
        return []
    # Sort experiment dirs descending to find the latest
    exp_dirs = sorted(
        (d for d in exp_base.iterdir() if d.is_dir()),
        key=lambda d: d.name,
        reverse=True,
    )
    for exp_dir in exp_dirs:
        eval_file = exp_dir / "eval_after.json"
        if eval_file.exists():
            try:
                data = json.loads(eval_file.read_text())
                results = data.get("results", [])
                return [
                    {
                        "name": r.get("name", ""),
                        "score": r.get("score", 0.0),
                        "weight": r.get("weight", 0.0),
                        "passed": r.get("passed", False),
                    }
                    for r in results
                ]
            except (json.JSONDecodeError, OSError):
                continue
    return []
