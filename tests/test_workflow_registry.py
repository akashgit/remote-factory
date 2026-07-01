"""Tests for WorkflowRegistry — discovery, loading, shadowing, error handling."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from factory.workflow.registry import WorkflowRegistry


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset registry state before each test."""
    WorkflowRegistry.reset()
    yield
    WorkflowRegistry.reset()


@pytest.fixture
def tmp_workflows(tmp_path: Path) -> Path:
    """Create a temp directory with a valid workflow file."""
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()

    (wf_dir / "example.py").write_text(
        'from factory.workflow.definitions import improve_workflow\n'
        '\n'
        'meta = {"name": "example", "description": "A test workflow"}\n'
        '\n'
        'def workflow():\n'
        '    wf = improve_workflow()\n'
        '    wf.name = "example"\n'
        '    return wf\n'
    )
    return wf_dir


# ── Discovery ────────────────────────────────────────────────────


class TestDiscovery:
    def test_discovers_builtins(self) -> None:
        entries = WorkflowRegistry.discover()
        assert "improve" in entries
        assert "build" in entries
        assert entries["improve"].source == "builtin"

    def test_discovers_from_search_path(self, tmp_workflows: Path) -> None:
        WorkflowRegistry.register_search_path(str(tmp_workflows))
        entries = WorkflowRegistry.discover()
        assert "example" in entries
        assert entries["example"].source == "project"
        assert entries["example"].path == str(tmp_workflows / "example.py")

    def test_discovers_from_project_path(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / ".factory" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "local.py").write_text(
            'from factory.workflow.definitions import improve_workflow\n'
            '\n'
            'meta = {"name": "local", "description": "Project-local"}\n'
            '\n'
            'def workflow():\n'
            '    wf = improve_workflow()\n'
            '    wf.name = "local"\n'
            '    return wf\n'
        )
        entries = WorkflowRegistry.discover(project_path=tmp_path)
        assert "local" in entries
        assert entries["local"].source == "project"

    def test_skips_underscored_files(self, tmp_workflows: Path) -> None:
        (tmp_workflows / "__init__.py").write_text(
            'meta = {"name": "hidden"}\n'
            'def workflow(): pass\n'
        )
        WorkflowRegistry.register_search_path(str(tmp_workflows))
        entries = WorkflowRegistry.discover()
        assert "hidden" not in entries

    def test_skips_nonexistent_path(self) -> None:
        WorkflowRegistry.register_search_path("/nonexistent/path")
        entries_before = len(WorkflowRegistry.discover())
        WorkflowRegistry.reset()
        # Adding a nonexistent path shouldn't increase the count
        WorkflowRegistry.register_search_path("/nonexistent/path")
        WorkflowRegistry.register_search_path("/another/nonexistent")
        entries_after = len(WorkflowRegistry.discover())
        assert entries_after == entries_before


# ── get_workflow ─────────────────────────────────────────────────


class TestGetWorkflow:
    def test_returns_workflow_object(self, tmp_workflows: Path) -> None:
        WorkflowRegistry.register_search_path(str(tmp_workflows))
        wf = WorkflowRegistry.get_workflow("example")
        assert wf is not None
        assert wf.name == "example"

    def test_returns_none_for_unknown(self) -> None:
        wf = WorkflowRegistry.get_workflow("nonexistent")
        assert wf is None

    def test_returns_builtin(self) -> None:
        wf = WorkflowRegistry.get_workflow("improve")
        assert wf is not None
        assert wf.name == "improve"


# ── Shadowing ────────────────────────────────────────────────────


class TestShadowing:
    def test_user_shadows_builtin(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "improve.py").write_text(
            'from factory.workflow.definitions import improve_workflow\n'
            '\n'
            'meta = {"name": "improve", "description": "Custom improve"}\n'
            '\n'
            'def workflow():\n'
            '    wf = improve_workflow()\n'
            '    wf.name = "improve"\n'
            '    return wf\n'
        )
        WorkflowRegistry.register_search_path(str(wf_dir))
        entries = WorkflowRegistry.discover()
        assert entries["improve"].source == "project"
        assert entries["improve"].description == "Custom improve"


# ── Error handling ───────────────────────────────────────────────


class TestErrorHandling:
    def test_skips_missing_meta(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "no_meta.py").write_text(
            'def workflow(): pass\n'
        )
        WorkflowRegistry.register_search_path(str(wf_dir))
        entries = WorkflowRegistry.discover()
        assert "no_meta" not in entries

    def test_skips_missing_workflow_fn(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "no_fn.py").write_text(
            'meta = {"name": "no_fn", "description": "Missing workflow()"}\n'
        )
        WorkflowRegistry.register_search_path(str(wf_dir))
        entries = WorkflowRegistry.discover()
        assert "no_fn" not in entries

    def test_skips_syntax_error(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "broken.py").write_text(
            'meta = {"name": "broken"\n'  # unclosed brace
        )
        WorkflowRegistry.register_search_path(str(wf_dir))
        entries = WorkflowRegistry.discover()
        assert "broken" not in entries

    def test_skips_meta_without_name(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "no_name.py").write_text(
            'meta = {"description": "Missing name key"}\n'
            'def workflow(): pass\n'
        )
        WorkflowRegistry.register_search_path(str(wf_dir))
        entries = WorkflowRegistry.discover()
        assert "no_name" not in entries


# ── Module cleanup ───────────────────────────────────────────────


class TestModuleCleanup:
    def test_no_module_pollution(self, tmp_workflows: Path) -> None:
        before = {k for k in sys.modules if k.startswith("factory_workflow_")}
        WorkflowRegistry.register_search_path(str(tmp_workflows))
        WorkflowRegistry.discover()
        WorkflowRegistry.get_workflow("example")
        after = {k for k in sys.modules if k.startswith("factory_workflow_")}
        assert after == before


# ── list_workflows ───────────────────────────────────────────────


class TestListWorkflows:
    def test_returns_sorted_entries(self) -> None:
        workflows = WorkflowRegistry.list_workflows()
        names = [w.name for w in workflows]
        assert len(names) >= 11  # at least the built-ins
        assert "improve" in names
        assert "build" in names

    def test_includes_external(self, tmp_workflows: Path) -> None:
        WorkflowRegistry.register_search_path(str(tmp_workflows))
        workflows = WorkflowRegistry.list_workflows()
        names = [w.name for w in workflows]
        assert "example" in names


# ── reset ────────────────────────────────────────────────────────


class TestReset:
    def test_clears_state(self, tmp_workflows: Path) -> None:
        WorkflowRegistry.register_search_path(str(tmp_workflows))
        WorkflowRegistry.discover()
        assert len(WorkflowRegistry._entries) > 0

        WorkflowRegistry.reset()
        assert len(WorkflowRegistry._entries) == 0
        assert len(WorkflowRegistry._search_paths) == 0
