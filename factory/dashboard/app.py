"""FastAPI dashboard server — serves UI and SSE event stream."""

from __future__ import annotations

import asyncio
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from factory.events import load_events
from factory.visualizer import (
    MODE_PHASES,
    PHASES,
    get_phases_for_mode,
    infer_mode_from_artifacts,
    infer_state,
    phase_index,
)

log = structlog.get_logger()

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")

_STATIC_DIR = Path(__file__).parent / "static"

_VERDICT_RE = re.compile(r"\*\*Verdict:\*\*\s*(PROCEED|REDIRECT|ABORT)")
_RATIONALE_RE = re.compile(r"\*\*Rationale:\*\*\s*(.+?)(?:\n|$)")
_ISSUES_RE = re.compile(r"\*\*Issues found:\*\*\s*(.+?)(?:\n\*\*|\Z)", re.DOTALL)
_FAILURE_CAT_RE = re.compile(r"^\s*[-*]\s*\**([^:*]+?)\**\s*:\s*(\d+)")


def _read_text_safe(path: Path) -> str | None:
    """Return file contents or None if missing/unreadable."""
    try:
        return path.read_text()
    except OSError:
        return None


def _read_json_safe(path: Path) -> dict[str, Any] | None:
    """Return parsed JSON dict or None if missing/unreadable."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _parse_single_verdict(path: Path) -> dict[str, Any] | None:
    """Parse a ceo-verdict-*.md file into structured verdict data."""
    text = _read_text_safe(path)
    if not text:
        return None
    verdict_match = _VERDICT_RE.search(text)
    if not verdict_match:
        return None

    rationale_match = _RATIONALE_RE.search(text)
    rationale = rationale_match.group(1).strip() if rationale_match else ""

    issues: list[str] = []
    issues_match = _ISSUES_RE.search(text)
    if issues_match:
        issues_text = issues_match.group(1).strip()
        if issues_text.lower() not in ("none", ""):
            for line in issues_text.splitlines():
                line = line.strip().lstrip("- ")
                if line:
                    issues.append(line)

    return {
        "decision": verdict_match.group(1),
        "rationale": rationale,
        "issues": issues,
    }


def _parse_diff_stats(diff_text: str) -> dict[str, int]:
    """Count files changed, insertions, and deletions from a unified diff."""
    files = 0
    insertions = 0
    deletions = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            files += 1
        elif line.startswith("+") and not line.startswith("+++"):
            insertions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return {"files_changed": files, "insertions": insertions, "deletions": deletions}


def _parse_failure_categories(text: str) -> dict[str, int]:
    """Parse failure categories and counts from failure_analysis.md content."""
    categories: dict[str, int] = {}
    for line in text.splitlines():
        m = _FAILURE_CAT_RE.match(line)
        if m:
            cat = m.group(1).strip()
            count = int(m.group(2))
            categories[cat] = categories.get(cat, 0) + count
    return categories


def _load_research_runs(factory_dir: Path) -> dict[str, Any]:
    """Load research run data from .factory/research/runs/ directory."""
    empty: dict[str, Any] = {
        "cycles": [],
        "failure_distribution": {},
        "ratchet": {"labels": [], "scores": [], "best": []},
    }
    runs_dir = factory_dir / "research" / "runs"
    if not runs_dir.exists():
        return empty

    cycles: list[dict[str, Any]] = []
    all_failures: dict[str, int] = {}

    run_dirs = sorted(
        (d for d in runs_dir.iterdir() if d.is_dir()),
        key=lambda d: d.name,
    )

    prev_score: float | None = None
    for run_dir in run_dirs:
        summary = _read_json_safe(run_dir / "summary.json")
        if summary is None:
            continue

        metric_value = summary.get("metric_value")
        delta: float | None = None
        if metric_value is not None and prev_score is not None:
            delta = round(metric_value - prev_score, 4)
        prev_score = metric_value

        failure_cats: dict[str, int] = {}
        failure_md = _read_text_safe(run_dir / "failure_analysis.md")
        if failure_md:
            failure_cats = _parse_failure_categories(failure_md)
            for cat, count in failure_cats.items():
                all_failures[cat] = all_failures.get(cat, 0) + count

        dominant_failure: str | None = None
        if failure_cats:
            dominant_failure = max(failure_cats, key=failure_cats.get)  # type: ignore[arg-type]

        cycles.append({
            "name": run_dir.name,
            "metric_value": metric_value,
            "status": summary.get("status"),
            "duration": summary.get("duration"),
            "delta": delta,
            "dominant_failure": dominant_failure,
        })

    labels = [c["name"] for c in cycles]
    scores = [c["metric_value"] for c in cycles]
    best: list[float | None] = []
    current_best: float | None = None
    for s in scores:
        if s is not None:
            if current_best is None or s > current_best:
                current_best = s
        best.append(current_best)

    return {
        "cycles": cycles,
        "failure_distribution": all_failures,
        "ratchet": {"labels": labels, "scores": scores, "best": best},
    }


def _resolve_experiment_id(
    project_path: Path, events: list[dict[str, Any]]
) -> int | None:
    """Resolve the current or latest experiment ID."""
    tail = events[-500:] if len(events) > 500 else events
    state = infer_state(tail)
    if state.current_experiment and state.current_experiment.get("id") is not None:
        return int(state.current_experiment["id"])

    tsv_path = project_path / ".factory" / "results.tsv"
    if tsv_path.exists():
        rows = _load_tsv(tsv_path)
        if rows:
            try:
                return int(rows[-1]["id"])
            except (KeyError, ValueError):
                pass
    return None


def _phase_data_detect(
    factory_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    config = _read_json_safe(factory_dir / "config.json") or {}
    profile = _read_json_safe(factory_dir / "eval_profile.json") or {}
    dims = profile.get("dimensions", [])
    return {
        "goal": config.get("goal", ""),
        "scope": config.get("scope", []),
        "eval_threshold": config.get("eval_threshold"),
        "eval_command": config.get("eval_command", ""),
        "constraints": config.get("constraints", []),
        "guards": config.get("guards", []),
        "language": profile.get("project_type", ""),
        "dimensions_count": len(dims),
    }, None


def _phase_data_discover(
    factory_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    profile = _read_json_safe(factory_dir / "eval_profile.json") or {}
    dims = profile.get("dimensions", [])
    return {
        "project_type": profile.get("project_type", ""),
        "confidence": profile.get("confidence"),
        "human_reviewed": profile.get("human_reviewed", False),
        "dimensions": [
            {
                "name": d.get("name", ""),
                "weight": d.get("weight", 0),
                "source": d.get("source", ""),
                "description": d.get("description", ""),
            }
            for d in dims
        ],
    }, None


def _phase_data_research(
    factory_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    verdict = _parse_single_verdict(
        factory_dir / "reviews" / "ceo-verdict-researcher.md"
    )
    return {
        "research": _read_text_safe(factory_dir / "strategy" / "research.md") or "",
        "observations": _read_text_safe(
            factory_dir / "strategy" / "observations.md"
        )
        or "",
        "agent_output": _read_text_safe(
            factory_dir / "reviews" / "researcher-latest.md"
        )
        or "",
    }, verdict


def _phase_data_strategize(
    factory_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    verdict = _parse_single_verdict(
        factory_dir / "reviews" / "ceo-verdict-strategist.md"
    )
    return {
        "strategy": _read_text_safe(factory_dir / "strategy" / "current.md") or "",
        "backlog": _read_text_safe(factory_dir / "strategy" / "backlog.md") or "",
        "agent_output": _read_text_safe(
            factory_dir / "reviews" / "strategist-latest.md"
        )
        or "",
    }, verdict


def _phase_data_build(
    factory_dir: Path, exp_id: int | None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    verdict = _parse_single_verdict(
        factory_dir / "reviews" / "ceo-verdict-builder.md"
    )
    data: dict[str, Any] = {
        "experiment_id": exp_id,
        "hypothesis": "",
        "diff": "",
        "diff_stats": {"files_changed": 0, "insertions": 0, "deletions": 0},
        "agent_output": _read_text_safe(
            factory_dir / "reviews" / "builder-latest.md"
        )
        or "",
    }
    if exp_id is not None:
        exp_dir = factory_dir / "experiments" / str(exp_id).zfill(3)
        data["hypothesis"] = _read_text_safe(exp_dir / "hypothesis.md") or ""
        diff = _read_text_safe(exp_dir / "changes.diff") or ""
        data["diff"] = diff
        if diff:
            data["diff_stats"] = _parse_diff_stats(diff)
    return data, verdict


def _phase_data_review(
    factory_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    verdict = _parse_single_verdict(
        factory_dir / "reviews" / "ceo-verdict-qa.md"
    )
    return {
        "agent_output": _read_text_safe(
            factory_dir / "reviews" / "qa-latest.md"
        )
        or "",
    }, verdict


def _phase_data_eval(
    factory_dir: Path, exp_id: int | None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    data: dict[str, Any] = {
        "experiment_id": exp_id,
        "score_before": None,
        "score_after": None,
        "delta": None,
        "last_eval": _read_json_safe(factory_dir / "last_eval.json"),
        "agent_output": _read_text_safe(
            factory_dir / "reviews" / "qa-latest.md"
        )
        or "",
    }
    if exp_id is not None:
        exp_dir = factory_dir / "experiments" / str(exp_id).zfill(3)
        before = _read_json_safe(exp_dir / "eval_before.json")
        after = _read_json_safe(exp_dir / "eval_after.json")
        data["score_before"] = before
        data["score_after"] = after
        if before and after:
            try:
                data["delta"] = round(
                    float(after.get("total", 0)) - float(before.get("total", 0)), 4
                )
            except (TypeError, ValueError):
                pass
    return data, None


def _phase_data_archive(
    factory_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    return {
        "agent_output": _read_text_safe(
            factory_dir / "reviews" / "archivist-latest.md"
        )
        or "",
        "session_summary": _read_text_safe(
            factory_dir / "reviews" / "session-summary.md"
        )
        or "",
        "performance_report": _read_json_safe(
            factory_dir / "performance_report.json"
        ),
    }, None


def _validate_path_segment(value: str, label: str = "name") -> None:
    """Reject path segments that could escape the projects directory."""
    if not _SAFE_NAME_RE.match(value) or ".." in value:
        raise HTTPException(status_code=400, detail=f"Invalid {label}: {value}")


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

    @app.get("/api/projects/{name}/details")
    async def project_details(name: str) -> dict[str, Any]:
        _validate_path_segment(name)
        log.info(
            "dashboard_request",
            endpoint="/api/projects/{name}/details",
            project=name,
        )
        path = projects_dir / name
        factory_dir = path / ".factory"

        factory_md = _read_text_safe(path / "factory.md")
        config = _read_json_safe(factory_dir / "config.json")
        profile = _read_json_safe(factory_dir / "eval_profile.json")
        backlog = _read_text_safe(factory_dir / "strategy" / "backlog.md")
        checkpoint = _read_json_safe(factory_dir / "checkpoint.json")
        report = _read_json_safe(factory_dir / "performance_report.json")

        has_factory = factory_dir.exists()
        has_config = config is not None
        has_profile = profile is not None

        if has_config:
            status = "ready"
        elif has_profile:
            status = "pending_review"
        elif has_factory:
            status = "discovering"
        else:
            status = "new"

        return {
            "name": name,
            "status": status,
            "factory_md": factory_md,
            "config": config,
            "eval_profile": profile,
            "backlog": backlog,
            "checkpoint": checkpoint,
            "performance_report": report,
        }

    @app.get("/api/projects/{name}/state")
    async def project_state(name: str) -> dict[str, Any]:
        _validate_path_segment(name)
        log.info("dashboard_request", endpoint="/api/projects/{name}/state", project=name)
        path = projects_dir / name
        events = load_events(path)
        tail = events[-500:] if len(events) > 500 else events
        state = infer_state(tail)
        if state.current_mode is None:
            inferred = infer_mode_from_artifacts(path / ".factory")
            if inferred:
                state.current_mode = inferred
        return state.to_dict()

    @app.get("/api/projects/{name}/agent-output/{role}")
    async def agent_output(name: str, role: str) -> PlainTextResponse:
        _validate_path_segment(name)
        _validate_path_segment(role, "role")
        log.info(
            "dashboard_request",
            endpoint="/api/projects/{name}/agent-output/{role}",
            project=name,
            role=role,
        )
        review_file = projects_dir / name / ".factory" / "reviews" / f"{role}-latest.md"
        if not review_file.exists():
            return PlainTextResponse("No output available", status_code=404)
        try:
            content = review_file.read_text()
        except OSError:
            return PlainTextResponse("Failed to read output", status_code=500)
        return PlainTextResponse(content)

    @app.get("/api/projects/{name}/phase-detail/{phase}")
    async def phase_detail(name: str, phase: str) -> JSONResponse:
        _validate_path_segment(name)
        _validate_path_segment(phase, "phase")
        log.info(
            "dashboard_request",
            endpoint="/api/projects/{name}/phase-detail/{phase}",
            project=name,
            phase=phase,
        )
        path = projects_dir / name
        factory_dir = path / ".factory"
        events = load_events(path)
        tail = events[-500:] if len(events) > 500 else events
        state = infer_state(tail)
        mode = state.current_mode
        if mode is None:
            mode = infer_mode_from_artifacts(factory_dir)

        mode_phases_list = get_phases_for_mode(mode)
        if phase not in mode_phases_list and phase not in PHASES:
            return JSONResponse(
                {"error": f"Invalid phase: {phase}"}, status_code=400
            )

        current_idx = phase_index(state.current_phase, mode)
        requested_idx = phase_index(phase, mode)
        if requested_idx < 0:
            requested_idx = phase_index(phase)
            current_idx = phase_index(state.current_phase)
        if requested_idx < 0:
            status = "future"
        elif current_idx < 0:
            status = "future"
        elif requested_idx < current_idx:
            status = "completed"
        elif requested_idx == current_idx:
            status = "active"
        else:
            status = "future"

        if status == "future":
            return JSONResponse(
                {"phase": phase, "status": "future", "data": None, "verdict": None}
            )

        # Resolve builder_key from mode phase definition
        builder_key: str | None = None
        mode_lower = (mode or "").lower()
        phase_defs = MODE_PHASES.get(mode_lower)
        if phase_defs:
            for display, bkey, _ in phase_defs:
                if display == phase:
                    builder_key = bkey
                    break
        if builder_key is None:
            builder_key = phase.lower()

        exp_id = _resolve_experiment_id(path, events)
        _PHASE_BUILDERS: dict[str, Any] = {
            "detect": lambda: _phase_data_detect(factory_dir),
            "discover": lambda: _phase_data_discover(factory_dir),
            "research": lambda: _phase_data_research(factory_dir),
            "strategize": lambda: _phase_data_strategize(factory_dir),
            "build": lambda: _phase_data_build(factory_dir, exp_id),
            "review": lambda: _phase_data_review(factory_dir),
            "eval": lambda: _phase_data_eval(factory_dir, exp_id),
            "archive": lambda: _phase_data_archive(factory_dir),
        }

        builder = _PHASE_BUILDERS.get(builder_key)
        if not builder:
            return JSONResponse(
                {"phase": phase, "status": status, "data": None, "verdict": None}
            )

        data, verdict = builder()
        return JSONResponse(
            {"phase": phase, "status": status, "data": data, "verdict": verdict}
        )

    @app.get("/api/projects/{name}/research-runs")
    async def project_research_runs(name: str) -> dict[str, Any]:
        _validate_path_segment(name)
        log.info(
            "dashboard_request",
            endpoint="/api/projects/{name}/research-runs",
            project=name,
        )
        path = projects_dir / name
        factory_dir = path / ".factory"
        return _load_research_runs(factory_dir)

    @app.get("/research/{name}", response_class=HTMLResponse)
    async def research_view(name: str) -> HTMLResponse:
        _validate_path_segment(name)
        log.info(
            "dashboard_request", endpoint="/research/{name}", project=name
        )
        return HTMLResponse((_STATIC_DIR / "research.html").read_text())

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


def _load_tsv(path: Path) -> list[dict[str, Any]]:
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
    """Load dimensions from last_eval.json or most recent experiment's eval_after.json."""

    def _parse_results(data: dict) -> list[dict[str, Any]]:
        return [
            {
                "name": r.get("name", ""),
                "score": r.get("score", 0.0),
                "weight": r.get("weight", 0.0),
                "passed": r.get("passed", False),
            }
            for r in data.get("results", [])
        ]

    # Primary: check .factory/last_eval.json (written by eval runner)
    last_eval = project_path / ".factory" / "last_eval.json"
    if last_eval.exists():
        try:
            data = json.loads(last_eval.read_text())
            results = _parse_results(data)
            if results:
                return results
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback: scan experiment dirs for eval_after.json
    exp_base = project_path / ".factory" / "experiments"
    if not exp_base.exists():
        return []
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
                return _parse_results(data)
            except (json.JSONDecodeError, OSError):
                continue
    return []
