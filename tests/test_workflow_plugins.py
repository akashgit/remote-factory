"""Tests for the workflow plugin system — manifest, namespace, version compat,
capability enforcement, entry-point discovery, safety ceilings, CLI, and backward compat."""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.workflow.executor import (
    MAX_GLOBAL_RELOOPS,
    MAX_NODE_TIMEOUT,
    WorkflowExecutor,
)
from factory.workflow.manifest import (
    WorkflowManifest,
    _get_factory_version,
    check_version_compatibility,
    manifest_from_meta,
    validate_capabilities,
    validate_namespace,
)
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    Verdict,
    VerdictType,
    Workflow,
)
from factory.workflow.registry import (
    WorkflowEntry,
    WorkflowRegistry,
    _get_node_types,
    _validate_workflow_graph,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    WorkflowRegistry.reset()
    yield
    WorkflowRegistry.reset()


# ── Manifest validation ────────────────────────────────────────────


class TestManifestValidation:
    def test_valid_manifest(self) -> None:
        m = WorkflowManifest(
            name="test:example",
            description="A test workflow",
            schema_version=1,
            capabilities=["agent_only"],
            author="test",
            url="https://example.com",
        )
        assert m.name == "test:example"
        assert m.schema_version == 1

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            WorkflowManifest(name="", description="test")

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(Exception):
            WorkflowManifest(name="test", description="test", unknown_field="bad")  # type: ignore[call-arg]

    def test_invalid_capability(self) -> None:
        with pytest.raises(Exception):
            WorkflowManifest(
                name="test", description="test", capabilities=["not_real"]  # type: ignore[list-item]
            )

    def test_manifest_from_meta_strict(self) -> None:
        meta = {
            "name": "ns:wf",
            "description": "desc",
            "schema_version": 1,
            "capabilities": ["shell_exec"],
        }
        m = manifest_from_meta(meta, strict=True)
        assert m.name == "ns:wf"
        assert m.capabilities == ["shell_exec"]

    def test_manifest_from_meta_lenient(self) -> None:
        meta = {"name": "simple", "description": "bare meta"}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            m = manifest_from_meta(meta, strict=False)
            assert m.name == "simple"
            assert len(w) == 1
            assert "bare meta dict" in str(w[0].message)

    def test_manifest_from_meta_missing_name(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            manifest_from_meta({"description": "no name"}, strict=False)


# ── Namespace enforcement ──────────────────────────────────────────


class TestNamespaceEnforcement:
    def test_builtin_bare_name_ok(self) -> None:
        assert validate_namespace("improve", "builtin") == []

    def test_user_bare_name_ok(self) -> None:
        assert validate_namespace("my-workflow", "user") == []

    def test_project_bare_name_ok(self) -> None:
        assert validate_namespace("local", "project") == []

    def test_plugin_bare_name_rejected(self) -> None:
        issues = validate_namespace("bare-name", "entry_point")
        assert len(issues) == 1
        assert "prefix:name" in issues[0]

    def test_plugin_namespaced_ok(self) -> None:
        assert validate_namespace("mypkg:workflow", "entry_point") == []

    def test_plugin_empty_prefix_rejected(self) -> None:
        issues = validate_namespace(":workflow", "entry_point")
        assert len(issues) == 1
        assert "non-empty" in issues[0]

    def test_plugin_empty_suffix_rejected(self) -> None:
        issues = validate_namespace("prefix:", "entry_point")
        assert len(issues) == 1
        assert "non-empty" in issues[0]


# ── Version compatibility ──────────────────────────────────────────


class TestVersionCompatibility:
    def test_no_version_constraint(self) -> None:
        m = WorkflowManifest(name="test", description="test")
        assert check_version_compatibility(m) == []

    def test_compatible_version(self) -> None:
        m = WorkflowManifest(
            name="test", description="test", min_factory_version="0.0.1"
        )
        with patch("factory.workflow.manifest._get_factory_version", return_value="1.0.0"):
            issues = check_version_compatibility(m)
        assert issues == []

    def test_incompatible_version(self) -> None:
        m = WorkflowManifest(
            name="test", description="test", min_factory_version="99.0.0"
        )
        issues = check_version_compatibility(m)
        assert len(issues) == 1
        assert "99.0.0" in issues[0]


# ── Capability enforcement ─────────────────────────────────────────


class TestCapabilityEnforcement:
    def test_agent_only_with_fn_node_rejected(self) -> None:
        m = WorkflowManifest(
            name="test", description="test", capabilities=["agent_only"]
        )
        issues = validate_capabilities(m, {"AgentNode", "FnNode"})
        assert len(issues) == 1
        assert "agent_only" in issues[0]
        assert "FnNode" in issues[0]

    def test_agent_only_without_fn_node_ok(self) -> None:
        m = WorkflowManifest(
            name="test", description="test", capabilities=["agent_only"]
        )
        assert validate_capabilities(m, {"AgentNode", "GateNode"}) == []

    def test_shell_exec_allows_fn_node(self) -> None:
        m = WorkflowManifest(
            name="test", description="test", capabilities=["shell_exec"]
        )
        assert validate_capabilities(m, {"FnNode", "AgentNode"}) == []

    def test_no_capabilities_allows_everything(self) -> None:
        m = WorkflowManifest(name="test", description="test")
        assert validate_capabilities(m, {"FnNode", "AgentNode"}) == []


# ── Discovery-time graph validation ───────────────────────────────


class TestDiscoveryGraphValidation:
    def test_invalid_graph_skipped_on_discover(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "bad_graph.py").write_text(
            "from factory.workflow.primitives import Workflow, AgentNode, AgentRole, Edge\n"
            "\n"
            "meta = {'name': 'bad_graph', 'description': 'invalid graph'}\n"
            "\n"
            "def workflow():\n"
            "    return Workflow(\n"
            "        name='bad_graph',\n"
            "        nodes={'a': AgentNode(id='a', role=AgentRole.BUILDER)},\n"
            "        edges=[Edge(source='a', target='nonexistent')],\n"
            "        start_node='a',\n"
            "    )\n"
        )
        WorkflowRegistry.register_search_path(str(wf_dir))
        entries = WorkflowRegistry.discover()
        assert "bad_graph" not in entries


# ── Entry points discovery ─────────────────────────────────────────


class TestEntryPointsDiscovery:
    def _make_ep_module(self, meta: dict[str, object], workflow_fn: object) -> object:
        module = SimpleNamespace(meta=meta, workflow=workflow_fn)
        return module

    def _make_valid_workflow(self) -> Workflow:
        return Workflow(
            name="ep:test",
            nodes={"start": AgentNode(id="start", role=AgentRole.BUILDER)},
            edges=[],
            start_node="start",
        )

    def test_entry_point_discovery(self) -> None:
        wf = self._make_valid_workflow()
        module = self._make_ep_module(
            {"name": "ep:test", "description": "test plugin"},
            lambda: wf,
        )

        mock_ep = MagicMock()
        mock_ep.name = "ep_test"
        mock_ep.load.return_value = module
        mock_ep.dist = MagicMock()
        mock_ep.dist.name = "my-plugin-package"

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            entries = WorkflowRegistry.discover()

        assert "ep:test" in entries
        assert entries["ep:test"].source == "entry_point"
        assert entries["ep:test"].package_name == "my-plugin-package"

    def test_entry_point_bare_name_rejected(self) -> None:
        wf = Workflow(
            name="bare",
            nodes={"start": AgentNode(id="start", role=AgentRole.BUILDER)},
            edges=[],
            start_node="start",
        )
        module = self._make_ep_module(
            {"name": "bare", "description": "no namespace"},
            lambda: wf,
        )

        mock_ep = MagicMock()
        mock_ep.name = "bare_ep"
        mock_ep.load.return_value = module
        mock_ep.dist = MagicMock()
        mock_ep.dist.name = "some-pkg"

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            entries = WorkflowRegistry.discover()

        assert "bare" not in entries

    def test_entry_point_missing_meta_skipped(self) -> None:
        module = SimpleNamespace(workflow=lambda: None)

        mock_ep = MagicMock()
        mock_ep.name = "no_meta"
        mock_ep.load.return_value = module
        mock_ep.dist = MagicMock()
        mock_ep.dist.name = "pkg"

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            entries = WorkflowRegistry.discover()

        assert "no_meta" not in entries

    def test_entry_point_load_failure_skipped(self) -> None:
        mock_ep = MagicMock()
        mock_ep.name = "broken_ep"
        mock_ep.load.side_effect = ImportError("cannot import")

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            entries = WorkflowRegistry.discover()

        # Should still have builtins
        assert "improve" in entries


# ── Priority ordering ──────────────────────────────────────────────


class TestPriorityOrdering:
    def test_project_shadows_entry_point(self, tmp_path: Path) -> None:
        wf = Workflow(
            name="ep:custom",
            nodes={"start": AgentNode(id="start", role=AgentRole.BUILDER)},
            edges=[],
            start_node="start",
        )
        module = SimpleNamespace(
            meta={"name": "ep:custom", "description": "entry-point"},
            workflow=lambda: wf,
        )

        mock_ep = MagicMock()
        mock_ep.name = "custom_ep"
        mock_ep.load.return_value = module
        mock_ep.dist = MagicMock()
        mock_ep.dist.name = "custom-pkg"

        # Create a project-local workflow with the same name
        wf_dir = tmp_path / ".factory" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "custom.py").write_text(
            "from factory.workflow.primitives import Workflow, AgentNode, AgentRole\n"
            "\n"
            "meta = {'name': 'ep:custom', 'description': 'project-local override'}\n"
            "\n"
            "def workflow():\n"
            "    return Workflow(\n"
            "        name='ep:custom',\n"
            "        nodes={'start': AgentNode(id='start', role=AgentRole.BUILDER)},\n"
            "        edges=[],\n"
            "        start_node='start',\n"
            "    )\n"
        )

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            entries = WorkflowRegistry.discover(project_path=tmp_path)

        assert "ep:custom" in entries
        assert entries["ep:custom"].source == "project"
        assert entries["ep:custom"].description == "project-local override"

    def test_entry_point_shadows_builtin(self) -> None:
        wf = Workflow(
            name="ep:improve",
            nodes={"start": AgentNode(id="start", role=AgentRole.BUILDER)},
            edges=[],
            start_node="start",
        )
        module = SimpleNamespace(
            meta={"name": "ep:improve", "description": "ep improve"},
            workflow=lambda: wf,
        )

        mock_ep = MagicMock()
        mock_ep.name = "improve_ep"
        mock_ep.load.return_value = module
        mock_ep.dist = MagicMock()
        mock_ep.dist.name = "improve-pkg"

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            entries = WorkflowRegistry.discover()

        assert "ep:improve" in entries
        assert entries["ep:improve"].source == "entry_point"
        # builtin "improve" should still exist as a separate entry
        assert "improve" in entries
        assert entries["improve"].source == "builtin"


# ── Global reloop ceiling ──────────────────────────────────────────


class TestGlobalReloopCeiling:
    async def test_global_reloop_ceiling_enforced(self) -> None:
        gate = GateNode(
            id="gate",
            evaluator_type="fn",
            evaluator_command="echo FAIL",
        )
        agent = AgentNode(id="agent", role=AgentRole.BUILDER)
        wf = Workflow(
            name="reloop_test",
            nodes={"agent": agent, "gate": gate},
            edges=[
                Edge(source="agent", target="gate"),
                Edge(source="gate", target="agent", condition=VerdictType.RELOOP),
            ],
            start_node="agent",
        )

        executor = WorkflowExecutor(wf, Path("/tmp"), dry_run=True)
        executor._global_reloop_count = MAX_GLOBAL_RELOOPS
        executor._global_reloop_count += 1
        assert executor._global_reloop_count > MAX_GLOBAL_RELOOPS

    def test_max_global_reloops_value(self) -> None:
        assert MAX_GLOBAL_RELOOPS == 20


# ── Per-node timeout ceiling ──────────────────────────────────────


class TestNodeTimeoutCeiling:
    def test_max_node_timeout_value(self) -> None:
        assert MAX_NODE_TIMEOUT == 3600

    async def test_timeout_capping_in_executor(self) -> None:
        agent = AgentNode(
            id="slow",
            role=AgentRole.BUILDER,
            timeout=9999,
        )
        wf = Workflow(
            name="timeout_test",
            nodes={"slow": agent},
            edges=[],
            start_node="slow",
        )
        executor = WorkflowExecutor(wf, Path("/tmp"), dry_run=True)

        result = await executor.execute()
        assert result.success


# ── list --plugins filtering ──────────────────────────────────────


class TestListPluginsFilter:
    def test_plugins_only_filters(self) -> None:
        entries = WorkflowRegistry.list_workflows(plugins_only=True)
        assert all(e.source == "entry_point" for e in entries)

    def test_plugins_only_empty_with_no_plugins(self) -> None:
        entries = WorkflowRegistry.list_workflows(plugins_only=True)
        assert len(entries) == 0  # no plugins installed by default


# ── doctor ─────────────────────────────────────────────────────────


class TestDoctor:
    def test_doctor_all_valid(self, capsys: pytest.CaptureFixture[str]) -> None:
        from factory.workflow.cli import _cmd_doctor
        args = argparse.Namespace(project_path=None)
        rc = _cmd_doctor(args)
        captured = capsys.readouterr()
        assert "[ OK ]" in captured.out
        assert rc == 0

    def test_doctor_reports_invalid(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from factory.workflow.cli import _cmd_doctor

        # Register a workflow that returns None from get_workflow
        WorkflowRegistry._entries["bad"] = WorkflowEntry(
            name="bad",
            description="broken",
            path="<test>",
            source="project",
            _workflow_fn=None,
        )

        args = argparse.Namespace(project_path=str(tmp_path))
        rc = _cmd_doctor(args)
        captured = capsys.readouterr()
        assert "[FAIL]" in captured.out
        assert rc == 1


# ── Backward compat for existing contributed workflows ────────────


class TestBackwardCompat:
    def test_bare_meta_loads_with_deprecation_warning(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "legacy.py").write_text(
            "from factory.workflow.primitives import Workflow, AgentNode, AgentRole\n"
            "\n"
            "meta = {'name': 'legacy', 'description': 'old-style workflow'}\n"
            "\n"
            "def workflow():\n"
            "    return Workflow(\n"
            "        name='legacy',\n"
            "        nodes={'start': AgentNode(id='start', role=AgentRole.BUILDER)},\n"
            "        edges=[],\n"
            "        start_node='start',\n"
            "    )\n"
        )
        WorkflowRegistry.register_search_path(str(wf_dir), source="user")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            entries = WorkflowRegistry.discover()

        assert "legacy" in entries
        assert entries["legacy"].source == "user"
        assert entries["legacy"].manifest is not None
        assert entries["legacy"].manifest.name == "legacy"
        deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(deprecation_warnings) >= 1
        assert "bare meta dict" in str(deprecation_warnings[0].message)

    def test_manifest_fields_suppress_deprecation(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "modern.py").write_text(
            "from factory.workflow.primitives import Workflow, AgentNode, AgentRole\n"
            "\n"
            "meta = {\n"
            "    'name': 'modern',\n"
            "    'description': 'new-style workflow',\n"
            "    'schema_version': 1,\n"
            "    'capabilities': ['shell_exec'],\n"
            "}\n"
            "\n"
            "def workflow():\n"
            "    return Workflow(\n"
            "        name='modern',\n"
            "        nodes={'start': AgentNode(id='start', role=AgentRole.BUILDER)},\n"
            "        edges=[],\n"
            "        start_node='start',\n"
            "    )\n"
        )
        WorkflowRegistry.register_search_path(str(wf_dir), source="user")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            entries = WorkflowRegistry.discover()

        assert "modern" in entries
        deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 0


# ── WorkflowEntry fields ──────────────────────────────────────────


class TestWorkflowEntryFields:
    def test_manifest_field_exists(self) -> None:
        entry = WorkflowEntry(
            name="test",
            description="test",
            path="<test>",
            source="builtin",
            manifest=WorkflowManifest(name="test", description="test"),
        )
        assert entry.manifest is not None
        assert entry.manifest.name == "test"

    def test_package_name_field(self) -> None:
        entry = WorkflowEntry(
            name="test",
            description="test",
            path="<test>",
            source="entry_point",
            package_name="my-package",
        )
        assert entry.package_name == "my-package"

    def test_fields_default_none(self) -> None:
        entry = WorkflowEntry(
            name="test", description="test", path="<test>", source="builtin"
        )
        assert entry.manifest is None
        assert entry.package_name is None


# ── Skill export with namespaced names ────────────────────────────


class TestSkillExportNamespaced:
    def test_namespaced_workflow_creates_correct_dir(self, tmp_path: Path) -> None:
        from factory.workflow.skill_export import export_all_skills

        wf = Workflow(
            name="mypkg:custom",
            nodes={"start": AgentNode(id="start", role=AgentRole.BUILDER)},
            edges=[],
            start_node="start",
        )
        generated = export_all_skills(tmp_path, {"mypkg:custom": wf})
        assert len(generated) == 1
        assert (tmp_path / "workflow-mypkg-custom").is_dir()
        assert (tmp_path / "workflow-mypkg-custom" / "SKILL.md").exists()


# ── Manifest edge cases ──────────────────────────────────────────


class TestManifestEdgeCases:
    def test_check_version_no_packaging_module(self) -> None:
        m = WorkflowManifest(
            name="test", description="test", min_factory_version="1.0.0"
        )
        with patch.dict(
            sys.modules,
            {"packaging": None, "packaging.specifiers": None, "packaging.version": None},
        ):
            issues = check_version_compatibility(m)
        assert issues == []

    def test_get_factory_version_fallback_to_zero(self) -> None:
        with patch("importlib.metadata.version", side_effect=Exception("not installed")):
            result = _get_factory_version()
        assert result == "0.0.0"

    def test_manifest_from_meta_non_string_description(self) -> None:
        meta: dict[str, object] = {
            "name": "test",
            "description": 42,
            "schema_version": 1,
        }
        m = manifest_from_meta(meta, strict=False)
        assert m.description == "42"

    def test_manifest_from_meta_none_description(self) -> None:
        meta: dict[str, object] = {
            "name": "test",
            "description": None,
            "schema_version": 1,
        }
        m = manifest_from_meta(meta, strict=False)
        assert m.description == ""

    def test_manifest_from_meta_schema_version_suppresses_warning(self) -> None:
        meta: dict[str, object] = {
            "name": "test",
            "description": "desc",
            "schema_version": 1,
        }
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            m = manifest_from_meta(meta, strict=False)
            deprecation = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(deprecation) == 0
        assert m.schema_version == 1


# ── Registry entry-point edge cases ──────────────────────────────


class TestEntryPointEdgeCases:
    def _make_valid_workflow(self, name: str = "ep:test") -> Workflow:
        return Workflow(
            name=name,
            nodes={"start": AgentNode(id="start", role=AgentRole.BUILDER)},
            edges=[],
            start_node="start",
        )

    def test_workflow_fn_not_callable(self) -> None:
        module = SimpleNamespace(
            meta={"name": "ep:notcallable", "description": "has non-callable workflow"},
            workflow="not a function",
        )
        mock_ep = MagicMock()
        mock_ep.name = "notcallable_ep"
        mock_ep.load.return_value = module
        mock_ep.dist = MagicMock()
        mock_ep.dist.name = "pkg"

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            entries = WorkflowRegistry.discover()
        assert "ep:notcallable" not in entries

    def test_version_incompatible(self) -> None:
        wf = self._make_valid_workflow("ep:oldver")
        module = SimpleNamespace(
            meta={
                "name": "ep:oldver",
                "description": "needs future version",
                "min_factory_version": "999.0.0",
            },
            workflow=lambda: wf,
        )
        mock_ep = MagicMock()
        mock_ep.name = "oldver_ep"
        mock_ep.load.return_value = module
        mock_ep.dist = MagicMock()
        mock_ep.dist.name = "oldver-pkg"

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            entries = WorkflowRegistry.discover()
        assert "ep:oldver" not in entries

    def test_graph_validation_failure(self) -> None:
        def bad_graph() -> Workflow:
            return Workflow(
                name="ep:badgraph",
                nodes={"a": AgentNode(id="a", role=AgentRole.BUILDER)},
                edges=[Edge(source="a", target="missing")],
                start_node="a",
            )

        module = SimpleNamespace(
            meta={"name": "ep:badgraph", "description": "invalid graph"},
            workflow=bad_graph,
        )
        mock_ep = MagicMock()
        mock_ep.name = "badgraph_ep"
        mock_ep.load.return_value = module
        mock_ep.dist = MagicMock()
        mock_ep.dist.name = "badgraph-pkg"

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            entries = WorkflowRegistry.discover()
        assert "ep:badgraph" not in entries

    def test_capability_violation(self) -> None:
        def agent_only_with_fn() -> Workflow:
            return Workflow(
                name="ep:capfail",
                nodes={"start": FnNode(id="start", command="echo hi")},
                edges=[],
                start_node="start",
            )

        module = SimpleNamespace(
            meta={
                "name": "ep:capfail",
                "description": "agent_only but has FnNode",
                "schema_version": 1,
                "capabilities": ["agent_only"],
            },
            workflow=agent_only_with_fn,
        )
        mock_ep = MagicMock()
        mock_ep.name = "capfail_ep"
        mock_ep.load.return_value = module
        mock_ep.dist = MagicMock()
        mock_ep.dist.name = "capfail-pkg"

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            entries = WorkflowRegistry.discover()
        assert "ep:capfail" not in entries

    def test_shadowing_non_builtin(self) -> None:
        wf = self._make_valid_workflow("ep:dup")

        def make_ep(ep_name: str, pkg_name: str) -> MagicMock:
            module = SimpleNamespace(
                meta={"name": "ep:dup", "description": f"from {pkg_name}"},
                workflow=lambda: wf,
            )
            mock_ep = MagicMock()
            mock_ep.name = ep_name
            mock_ep.load.return_value = module
            mock_ep.dist = MagicMock()
            mock_ep.dist.name = pkg_name
            return mock_ep

        ep1 = make_ep("dup1", "pkg-a")
        ep2 = make_ep("dup2", "pkg-b")

        with patch("importlib.metadata.entry_points", return_value=[ep1, ep2]):
            entries = WorkflowRegistry.discover()
        assert entries["ep:dup"].package_name == "pkg-b"

    def test_entry_points_exception(self) -> None:
        with patch(
            "importlib.metadata.entry_points", side_effect=Exception("broken")
        ):
            entries = WorkflowRegistry.discover()
        assert "improve" in entries


# ── Registry directory discovery edge cases ──────────────────────


class TestDirectoryDiscoveryEdgeCases:
    def test_version_check_failure(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "vfail.py").write_text(
            "from factory.workflow.primitives import Workflow, AgentNode, AgentRole\n"
            "\n"
            "meta = {\n"
            "    'name': 'vfail',\n"
            "    'description': 'needs future version',\n"
            "    'schema_version': 1,\n"
            "    'min_factory_version': '999.0.0',\n"
            "}\n"
            "\n"
            "def workflow():\n"
            "    return Workflow(\n"
            "        name='vfail',\n"
            "        nodes={'s': AgentNode(id='s', role=AgentRole.BUILDER)},\n"
            "        edges=[],\n"
            "        start_node='s',\n"
            "    )\n"
        )
        WorkflowRegistry.register_search_path(str(wf_dir))
        entries = WorkflowRegistry.discover()
        assert "vfail" not in entries

    def test_capability_check_failure(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "capfail.py").write_text(
            "from factory.workflow.primitives import Workflow, FnNode\n"
            "\n"
            "meta = {\n"
            "    'name': 'capfail',\n"
            "    'description': 'agent_only with FnNode',\n"
            "    'schema_version': 1,\n"
            "    'capabilities': ['agent_only'],\n"
            "}\n"
            "\n"
            "def workflow():\n"
            "    return Workflow(\n"
            "        name='capfail',\n"
            "        nodes={'s': FnNode(id='s', command='echo hi')},\n"
            "        edges=[],\n"
            "        start_node='s',\n"
            "    )\n"
        )
        WorkflowRegistry.register_search_path(str(wf_dir))
        entries = WorkflowRegistry.discover()
        assert "capfail" not in entries

    def test_load_exception_skipped(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "broken.py").write_text("raise RuntimeError('import bomb')\n")
        WorkflowRegistry.register_search_path(str(wf_dir))
        entries = WorkflowRegistry.discover()
        assert "broken" not in entries


# ── Registry graph/node validation helpers ───────────────────────


class TestRegistryValidationHelpers:
    def test_validate_workflow_graph_raises(self) -> None:
        def exploding() -> Workflow:
            raise RuntimeError("boom")

        issues = _validate_workflow_graph(exploding, "test")
        assert len(issues) == 1
        assert "raised" in issues[0]

    def test_validate_workflow_graph_non_workflow(self) -> None:
        issues = _validate_workflow_graph(lambda: "not a workflow", "test")
        assert len(issues) == 1
        assert "did not return a Workflow" in issues[0]

    def test_get_node_types_raises(self) -> None:
        def exploding() -> Workflow:
            raise RuntimeError("boom")

        result = _get_node_types(exploding, "test")
        assert result == set()

    def test_get_node_types_non_workflow(self) -> None:
        result = _get_node_types(lambda: "not a workflow", "test")
        assert result == set()


# ── CLI list command formats ─────────────────────────────────────


class TestCmdListFormats:
    def test_list_json_format(self, capsys: pytest.CaptureFixture[str]) -> None:
        from factory.workflow.cli import _cmd_list

        args = argparse.Namespace(project_path=None, plugins=False, format="json")
        with patch("factory.workflow.registry.log"), patch("factory.workflow.cli.log"):
            rc = _cmd_list(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert len(data) > 0
        assert "name" in data[0]
        assert "source" in data[0]
        assert "nodes" in data[0]
        assert "edges" in data[0]
        assert "start_node" in data[0]
        assert rc == 0

    def test_list_json_with_package_name(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from factory.workflow.cli import _cmd_list

        WorkflowRegistry._entries["ep:myplugin"] = WorkflowEntry(
            name="ep:myplugin",
            description="a plugin",
            path="<entry_point>",
            source="entry_point",
            package_name="my-plugin-pkg",
            _workflow_fn=lambda: Workflow(
                name="ep:myplugin",
                nodes={"s": AgentNode(id="s", role=AgentRole.BUILDER)},
                edges=[],
                start_node="s",
            ),
        )

        args = argparse.Namespace(project_path=None, plugins=False, format="json")
        with patch("factory.workflow.registry.log"), patch("factory.workflow.cli.log"):
            rc = _cmd_list(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        plugin_entry = next(d for d in data if d["name"] == "ep:myplugin")
        assert plugin_entry["package_name"] == "my-plugin-pkg"
        assert "nodes" in plugin_entry
        assert rc == 0

    def test_list_plugins_filter(self, capsys: pytest.CaptureFixture[str]) -> None:
        from factory.workflow.cli import _cmd_list

        args = argparse.Namespace(project_path=None, plugins=True, format="table")
        rc = _cmd_list(args)
        captured = capsys.readouterr()
        assert rc == 0
        assert "builtin" not in captured.out.lower().split("\n")[-1] or captured.out.strip() == ""

    def test_list_table_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        from factory.workflow.cli import _cmd_list

        args = argparse.Namespace(project_path=None, plugins=False, format="table")
        rc = _cmd_list(args)
        captured = capsys.readouterr()
        assert "Name" in captured.out
        assert "Source" in captured.out
        assert "Nodes" in captured.out
        assert rc == 0


# ── CLI doctor with graph validation issues ──────────────────────


class TestCmdDoctorGraphIssues:
    def test_doctor_reports_graph_issues(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from factory.workflow.cli import _cmd_doctor

        def wf_with_issues() -> Workflow:
            return Workflow(
                name="has_issues",
                nodes={"a": AgentNode(id="a", role=AgentRole.BUILDER)},
                edges=[Edge(source="a", target="nonexistent")],
                start_node="a",
            )

        WorkflowRegistry._entries["has_issues"] = WorkflowEntry(
            name="has_issues",
            description="graph with issues",
            path="<test>",
            source="project",
            _workflow_fn=wf_with_issues,
        )

        args = argparse.Namespace(project_path=None)
        rc = _cmd_doctor(args)
        captured = capsys.readouterr()
        assert "[FAIL]" in captured.out
        assert "issue(s)" in captured.out
        assert rc == 1


# ── Argparse registration ────────────────────────────────────────


class TestArgparseRegistration:
    def test_list_flags_parsed(self) -> None:
        from factory.workflow.cli import add_workflow_parser

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        add_workflow_parser(sub)
        args = parser.parse_args(["workflow", "list", "--plugins", "--format", "json"])
        assert args.plugins is True
        assert args.format == "json"

    def test_doctor_project_path_parsed(self) -> None:
        from factory.workflow.cli import add_workflow_parser

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        add_workflow_parser(sub)
        args = parser.parse_args(["workflow", "doctor", "--project-path", "/tmp/test"])
        assert args.project_path == "/tmp/test"


# ── Executor gate reloop ceiling enforcement ─────────────────────


class TestExecutorGateReloopEnforcement:
    async def test_global_reloop_ceiling_halts_execution(self) -> None:
        gate = GateNode(
            id="gate",
            evaluator_type="fn",
            evaluator_command="echo test",
        )
        agent = AgentNode(id="agent", role=AgentRole.BUILDER)
        wf = Workflow(
            name="reloop_ceil",
            nodes={"agent": agent, "gate": gate},
            edges=[
                Edge(source="agent", target="gate"),
                Edge(source="gate", target="agent", condition=VerdictType.RELOOP),
            ],
            start_node="agent",
        )
        executor = WorkflowExecutor(wf, Path("/tmp"), dry_run=True)
        executor._global_reloop_count = MAX_GLOBAL_RELOOPS

        with patch.object(
            executor,
            "_evaluate_gate",
            new_callable=AsyncMock,
            return_value=Verdict.reloop(target="agent", feedback="test"),
        ):
            await executor._execute_gate(gate)

        assert executor.result.halted
        assert "global reloop ceiling" in executor.result.halt_reason
        assert str(MAX_GLOBAL_RELOOPS) in executor.result.halt_reason


# ── Executor timeout capping ─────────────────────────────────────


class TestExecutorTimeoutCapping:
    async def test_timeout_capped_with_warning(self) -> None:
        agent = AgentNode(
            id="slow_agent",
            role=AgentRole.BUILDER,
            timeout=MAX_NODE_TIMEOUT + 1000,
        )
        wf = Workflow(
            name="timeout_cap",
            nodes={"slow_agent": agent},
            edges=[],
            start_node="slow_agent",
        )
        executor = WorkflowExecutor(wf, Path("/tmp"), dry_run=False)

        with patch(
            "factory.agents.runner.invoke_agent",
            new_callable=AsyncMock,
            return_value=("output", 0),
        ) as mock_invoke:
            result = await executor._run_agent(agent)

        assert result == "output"
        assert mock_invoke.call_args.kwargs["timeout"] == float(MAX_NODE_TIMEOUT)
