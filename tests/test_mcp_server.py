"""Tests for the MCP server tool handlers."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path

import pytest

from factory.mcp_server import (
    handle_get_score,
    handle_get_status,
    handle_list_experiments,
    handle_list_projects,
    list_tools,
)
from factory.store import TSV_COLUMNS


@pytest.fixture
def factory_project(tmp_project: Path) -> Path:
    """Create a tmp_project with .factory/ directory and sample data."""
    factory_dir = tmp_project / ".factory"
    factory_dir.mkdir()
    (factory_dir / "experiments").mkdir()
    (factory_dir / "strategy").mkdir()

    # config.json
    config = {
        "goal": "Build a test CLI",
        "scope": ["src/**/*.py"],
        "guards": ["no deletions"],
        "eval_command": "pytest",
        "eval_threshold": 0.8,
        "constraints": ["keep it simple"],
    }
    (factory_dir / "config.json").write_text(json.dumps(config, indent=2))

    # results.tsv with 2 experiments
    buf = io.StringIO()
    writer = csv.writer(buf, dialect="excel-tab")
    writer.writerow(TSV_COLUMNS)
    writer.writerow([
        1, datetime(2025, 1, 1, 12, 0).isoformat(), "Add logging", "Added structlog",
        "", "", "0.5", "0.6", "0.1", "keep", "0.50", "first experiment", "",
    ])
    writer.writerow([
        2, datetime(2025, 1, 2, 12, 0).isoformat(), "Add tests", "Added pytest suite",
        "10", "11", "0.6", "0.7", "0.1", "keep", "0.75", "second experiment", "",
    ])
    (factory_dir / "results.tsv").write_text(buf.getvalue())

    # last_eval.json
    last_eval = {
        "total": 0.75,
        "results": [
            {"name": "tests", "score": 0.9, "weight": 1.0, "passed": True, "details": "ok"}
        ],
        "guard_violations": [],
        "passed": True,
    }
    (factory_dir / "last_eval.json").write_text(json.dumps(last_eval, indent=2))

    return tmp_project


# ── factory_get_score ────────────────────────────────────────────


async def test_get_score_returns_eval_json(factory_project: Path) -> None:
    result = await handle_get_score(str(factory_project))
    data = json.loads(result)
    assert data["total"] == 0.75
    assert data["passed"] is True
    assert len(data["results"]) == 1


async def test_get_score_missing_file(tmp_project: Path) -> None:
    result = await handle_get_score(str(tmp_project))
    data = json.loads(result)
    assert "error" in data


# ── factory_list_experiments ─────────────────────────────────────


async def test_list_experiments_returns_records(factory_project: Path) -> None:
    result = await handle_list_experiments(str(factory_project))
    data = json.loads(result)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["hypothesis"] == "Add logging"
    assert data[1]["hypothesis"] == "Add tests"


async def test_list_experiments_last_n(factory_project: Path) -> None:
    result = await handle_list_experiments(str(factory_project), last_n=1)
    data = json.loads(result)
    assert len(data) == 1
    assert data[0]["hypothesis"] == "Add tests"


async def test_list_experiments_no_factory(tmp_project: Path) -> None:
    result = await handle_list_experiments(str(tmp_project))
    data = json.loads(result)
    assert "error" in data


# ── factory_get_status ───────────────────────────────────────────


async def test_get_status_with_factory(factory_project: Path) -> None:
    result = await handle_get_status(str(factory_project))
    data = json.loads(result)
    assert data["state"] == "has_factory"
    assert "config" in data
    assert data["config"]["goal"] == "Build a test CLI"


async def test_get_status_without_factory(tmp_project: Path) -> None:
    result = await handle_get_status(str(tmp_project))
    data = json.loads(result)
    assert data["state"] == "no_factory"
    assert "config" not in data


# ── factory_list_projects ────────────────────────────────────────


async def test_list_projects_finds_managed(factory_project: Path) -> None:
    """factory_project lives at tmp_path/test-project; scan tmp_path."""
    projects_dir = factory_project.parent
    result = await handle_list_projects(str(projects_dir))
    data = json.loads(result)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["name"] == factory_project.name
    assert data[0]["goal"] == "Build a test CLI"


async def test_list_projects_empty_dir(tmp_path: Path) -> None:
    result = await handle_list_projects(str(tmp_path))
    data = json.loads(result)
    assert data == []


async def test_list_projects_invalid_dir() -> None:
    result = await handle_list_projects("/nonexistent/path")
    data = json.loads(result)
    assert "error" in data


# ── list_tools ───────────────────────────────────────────────────


async def test_list_tools_returns_four_tools() -> None:
    tools = await list_tools()
    assert len(tools) == 4
    names = {t.name for t in tools}
    assert names == {
        "factory_get_score",
        "factory_list_experiments",
        "factory_get_status",
        "factory_list_projects",
    }
    # Every tool has a description and inputSchema
    for t in tools:
        assert t.description
        assert t.inputSchema
