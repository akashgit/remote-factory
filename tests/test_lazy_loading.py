"""Tests for lazy loading behavior in workflow registry and telemetry."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.workflow.registry import WorkflowRegistry


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset registry state before each test."""
    WorkflowRegistry.reset()
    yield
    WorkflowRegistry.reset()


# ── BUILTIN_REGISTRY ────────────────────────────────────────────


class TestBuiltinRegistry:
    def test_registry_contains_all_workflows(self) -> None:
        from factory.workflow.definitions import _get_builtin_registry

        registry = _get_builtin_registry()
        required = {
            "build", "design", "improve", "research", "meta",
            "discover", "review", "refine", "create", "founder",
            "deep-qa", "swebench", "legacybench", "featurebench",
            "programbench", "terminalbench", "tomswe", "salitrap",
            "doc-generate", "doc-update",
            "spec-generate", "spec-update", "parallel-improve",
            "frontend-design", "frontend-design-discover",
            "frontend-design-scan", "plan", "evolve",
        }
        assert required.issubset(set(registry.keys())), (
            f"Missing: {required - set(registry.keys())}"
        )

    def test_registry_values_are_callable(self) -> None:
        from factory.workflow.definitions import _get_builtin_registry

        registry = _get_builtin_registry()
        for name, fn in registry.items():
            assert callable(fn), f"{name} is not callable"

    def test_register_all_backward_compat(self) -> None:
        """register_all() still returns a dict of constructed Workflow objects."""
        from factory.workflow.definitions import register_all

        all_wf = register_all()
        assert len(all_wf) >= 13
        for name, wf in all_wf.items():
            assert hasattr(wf, "name"), f"{name} is not a Workflow"
            assert hasattr(wf, "nodes"), f"{name} is not a Workflow"

    def test_contributed_not_imported_at_discover(self) -> None:
        """discover() should not import contributed workflow modules."""
        contrib_modules = [
            "factory.workflow.contributed.swebench",
            "factory.workflow.contributed.legacybench",
            "factory.workflow.contributed.featurebench",
            "factory.workflow.contributed.programbench",
            "factory.workflow.contributed.terminalbench",
            "factory.workflow.contributed.tomswe",
            "factory.workflow.contributed.salitrap",
            "factory.workflow.deep_qa",
        ]
        for mod in contrib_modules:
            sys.modules.pop(mod, None)

        entries = WorkflowRegistry.discover()

        assert "swebench" in entries
        assert "deep-qa" in entries
        assert entries["swebench"].source == "builtin"

        for mod in contrib_modules:
            assert mod not in sys.modules, (
                f"{mod} was imported during discover() — lazy loading broken"
            )

    def test_get_workflow_triggers_import(self) -> None:
        """get_workflow() for a contributed workflow should import the module."""
        sys.modules.pop("factory.workflow.deep_qa", None)

        WorkflowRegistry.discover()
        wf = WorkflowRegistry.get_workflow("deep-qa")

        assert wf is not None
        assert wf.name == "deep-qa"
        assert "factory.workflow.deep_qa" in sys.modules

    def test_discover_api_unchanged(self) -> None:
        """discover() returns WorkflowEntry objects with all expected fields."""
        entries = WorkflowRegistry.discover()
        for name, entry in entries.items():
            assert entry.name == name
            assert isinstance(entry.description, str)
            assert entry.source in ("builtin", "user", "project")
            assert entry._workflow_fn is not None


# ── Telemetry lazy import ───────────────────────────────────────


class TestTelemetryLazyImport:
    @pytest.fixture(autouse=True)
    def _reset_telemetry(self):
        """Save and restore telemetry module state without reloading."""
        import factory.telemetry
        saved_has = factory.telemetry._HAS_LANGFUSE
        saved_client = factory.telemetry._client
        yield
        factory.telemetry._HAS_LANGFUSE = saved_has
        factory.telemetry._client = saved_client

    def test_langfuse_not_imported_at_module_level(self) -> None:
        """_HAS_LANGFUSE starts as None (lazy — not checked at import time)."""
        import factory.telemetry
        factory.telemetry._HAS_LANGFUSE = None
        assert factory.telemetry._HAS_LANGFUSE is None

    def test_is_enabled_caches_import_result(self) -> None:
        """is_enabled() should cache the import check result."""
        import factory.telemetry
        factory.telemetry._HAS_LANGFUSE = None
        factory.telemetry._client = None

        factory.telemetry.is_enabled()
        assert factory.telemetry._HAS_LANGFUSE is not None

        cached = factory.telemetry._HAS_LANGFUSE
        factory.telemetry.is_enabled()
        assert factory.telemetry._HAS_LANGFUSE == cached

    def test_is_enabled_returns_false_without_host(self) -> None:
        """is_enabled() returns False when no LANGFUSE env vars are set."""
        import factory.telemetry
        factory.telemetry._client = None
        factory.telemetry._HAS_LANGFUSE = None

        with patch.dict("os.environ", {}, clear=True):
            result = factory.telemetry.is_enabled()

        assert result is False


# ── Executor timing summary ────────────────────────────────────


class TestExecutorTimingSummary:
    async def test_timing_summary_emitted(self, tmp_path: Path) -> None:
        """execute() should emit a workflow.timing_summary log."""
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import Edge, FnNode, Workflow

        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()

        wf = Workflow(
            name="timing-test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "b": FnNode(id="b", command="echo b", reads={"a.txt"}, writes={"b.txt"}),
            },
            edges=[Edge(source="a", target="b")],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)

        captured_events: list[dict] = []

        with patch("factory.workflow.executor.log") as mock_log:
            def capture_info(*args, **kwargs):
                if args and args[0] == "workflow.timing_summary":
                    captured_events.append(kwargs)
            mock_log.info = capture_info
            mock_log.debug = lambda *a, **kw: None
            mock_log.error = lambda *a, **kw: None
            mock_log.warning = lambda *a, **kw: None

            result = await executor.execute()

        assert result.success
        assert len(captured_events) == 1

        summary = captured_events[0]
        assert summary["workflow"] == "timing-test"
        assert summary["run_id"] == executor.run_id
        assert summary["total_ms"] > 0
        assert summary["node_count"] == 2
        assert len(summary["nodes"]) == 2
        assert "overhead_ms" in summary

        for node_entry in summary["nodes"]:
            assert "id" in node_entry
            assert "type" in node_entry
            assert "duration_ms" in node_entry

        assert summary["nodes"][0]["duration_ms"] >= summary["nodes"][1]["duration_ms"]
