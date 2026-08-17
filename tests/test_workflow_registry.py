"""Tests for WorkflowRegistry — discovery, loading, error handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.workflow.registry import WorkflowRegistry


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset registry state before each test."""
    WorkflowRegistry.reset()
    yield
    WorkflowRegistry.reset()


# ── Discovery ────────────────────────────────────────────────────


class TestDiscovery:
    def test_discovers_builtins(self) -> None:
        entries = WorkflowRegistry.discover()
        assert "improve" in entries
        assert "build" in entries
        assert entries["improve"].source == "builtin"

    def test_discovers_from_project_path(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / ".factory" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "local.py").write_text(
            "from factory.workflow.definitions import improve_workflow\n"
            "\n"
            'meta = {"name": "local", "description": "Project-local"}\n'
            "\n"
            "def workflow():\n"
            "    wf = improve_workflow()\n"
            '    wf.name = "local"\n'
            "    return wf\n"
        )
        entries = WorkflowRegistry.discover(project_path=tmp_path)
        assert "local" in entries
        assert entries["local"].source == "project"


# ── get_workflow ─────────────────────────────────────────────────


class TestGetWorkflow:
    def test_returns_none_for_unknown(self) -> None:
        wf = WorkflowRegistry.get_workflow("nonexistent")
        assert wf is None

    def test_returns_builtin(self) -> None:
        wf = WorkflowRegistry.get_workflow("improve")
        assert wf is not None
        assert wf.name == "improve"


# ── list_workflows ───────────────────────────────────────────────


class TestListWorkflows:
    def test_returns_sorted_entries(self) -> None:
        workflows = WorkflowRegistry.list_workflows()
        names = [w.name for w in workflows]
        assert len(names) >= 11  # at least the built-ins
        assert "improve" in names
        assert "build" in names


# ── reset ────────────────────────────────────────────────────────


class TestReset:
    def test_clears_state(self) -> None:
        WorkflowRegistry.discover()
        assert len(WorkflowRegistry._entries) > 0

        WorkflowRegistry.reset()
        assert len(WorkflowRegistry._entries) == 0
        assert len(WorkflowRegistry._search_paths) == 0
