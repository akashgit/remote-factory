"""Graphify integration — extract, update, and query code knowledge graphs."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import structlog

log = structlog.get_logger()

GRAPH_DIR = ".factory/graphify-out"
GRAPH_FILE = "graph.json"
CACHE_DIR = "cache"


def _graph_dir(project_path: Path) -> Path:
    return project_path / GRAPH_DIR


def _graph_path(project_path: Path) -> Path:
    return _graph_dir(project_path) / GRAPH_FILE


def is_graphify_installed() -> bool:
    """Check whether the graphify CLI is available on PATH."""
    return shutil.which("graphify") is not None


def is_graph_available(project_path: Path) -> bool:
    """Check whether a graph.json exists for the given project."""
    return _graph_path(project_path).is_file()


def graph_stats(project_path: Path) -> dict[str, int] | None:
    """Return node/edge counts from graph.json, or None if unavailable."""
    gpath = _graph_path(project_path)
    if not gpath.is_file():
        return None
    try:
        data = json.loads(gpath.read_text(encoding="utf-8"))
        nodes = data.get("nodes", [])
        edges = data.get("edges", data.get("links", []))
        return {"nodes": len(nodes), "edges": len(edges)}
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("graph.stats.failed", error=str(exc))
        return None


def is_graph_stale(project_path: Path) -> bool | None:
    """Compare graph.json mtime against latest git commit timestamp.

    Returns True if stale, False if fresh, None if comparison not possible.
    """
    gpath = _graph_path(project_path)
    if not gpath.is_file():
        return None

    try:
        graph_mtime = gpath.stat().st_mtime
    except OSError:
        return None

    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        latest_commit_ts = float(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return None

    return graph_mtime < latest_commit_ts


def extract_graph(project_path: Path) -> Path | None:
    """Run graphify extract on the project directory.

    Stores output in .factory/graphify-out/graph.json.
    Returns path to graph.json on success, None on failure.
    """
    if not is_graphify_installed():
        log.warning("graph.extract.skipped", reason="graphify not installed")
        return None

    out_dir = _graph_dir(project_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [
                "graphify",
                "extract",
                str(project_path),
                "--output-dir",
                str(out_dir),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.error("graph.extract.failed", error=str(exc))
        return None

    if result.returncode != 0:
        log.error(
            "graph.extract.failed",
            returncode=result.returncode,
            stderr=result.stderr[:500],
        )
        return None

    gpath = _graph_path(project_path)
    if not gpath.is_file():
        log.error("graph.extract.no_output", expected=str(gpath))
        return None

    stats = graph_stats(project_path)
    log.info("graph.extract.complete", output=str(gpath), **(stats or {}))
    return gpath


def update_graph(project_path: Path) -> Path | None:
    """Run graphify extract with --update for incremental refresh.

    Returns path to graph.json on success, None on failure.
    """
    if not is_graphify_installed():
        log.warning("graph.update.skipped", reason="graphify not installed")
        return None

    out_dir = _graph_dir(project_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [
                "graphify",
                "extract",
                str(project_path),
                "--output-dir",
                str(out_dir),
                "--update",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.error("graph.update.failed", error=str(exc))
        return None

    if result.returncode != 0:
        log.error(
            "graph.update.failed",
            returncode=result.returncode,
            stderr=result.stderr[:500],
        )
        return None

    gpath = _graph_path(project_path)
    if not gpath.is_file():
        log.error("graph.update.no_output", expected=str(gpath))
        return None

    stats = graph_stats(project_path)
    log.info("graph.update.complete", output=str(gpath), **(stats or {}))
    return gpath


def load_graph_data(project_path: Path) -> dict | None:
    """Load raw graph.json as a dict. Returns None if unavailable."""
    gpath = _graph_path(project_path)
    if not gpath.is_file():
        return None
    try:
        return json.loads(gpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("graph.load.failed", error=str(exc))
        return None
