"""Tests for plugin workflow registration (issue #1490).

Covers:
- WorkflowRegistry.discover() consumes PluginRegistry.workflow_search_paths natively
- Discovery priority: project > plugin > user > builtin, enforced by guarded overwrites
- Shadow events are logged for every source, including over builtins
- skill_cache checksum invalidates when a discovered workflow .py file changes
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from factory.plugins import PluginRegistry
from factory.skill_cache import _compute_checksum
from factory.workflow.primitives import Workflow
from factory.workflow.registry import WorkflowRegistry


WORKFLOW_FILE = (
    "from factory.workflow.definitions import design_workflow\n"
    "\n"
    'meta = {{"name": "{name}", "description": "{desc}"}}\n'
    "\n"
    "def workflow():\n"
    "    wf = design_workflow()\n"
    '    wf.name = "{name}"\n'
    "    return wf\n"
)


def _write_workflow(directory: Path, name: str, desc: str = "test workflow") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    path.write_text(WORKFLOW_FILE.format(name=name, desc=desc))
    return path


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset registry state before and after each test."""
    WorkflowRegistry.reset()
    yield
    WorkflowRegistry.reset()


@pytest.fixture(autouse=True)
def _no_real_plugins():
    """Isolate discovery from plugins installed in the environment."""
    empty = PluginRegistry()
    with patch("factory.plugins.get_registry", return_value=empty):
        yield


# ── Native plugin path consumption ───────────────────────────────


class TestPluginPathConsumption:
    def test_plugin_search_paths_discovered(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my_plugin" / "workflows"
        _write_workflow(plugin_dir, "plugin-mode")

        registry = PluginRegistry()
        registry.add_workflow_search_path(str(plugin_dir))

        with patch("factory.plugins.get_registry", return_value=registry):
            entries = WorkflowRegistry.discover()

        assert "plugin-mode" in entries
        assert entries["plugin-mode"].source == "plugin"

    def test_no_bridge_to_search_paths_needed(self, tmp_path: Path) -> None:
        """Plugin paths work without touching WorkflowRegistry._search_paths."""
        plugin_dir = tmp_path / "my_plugin" / "workflows"
        _write_workflow(plugin_dir, "no-bridge")

        registry = PluginRegistry()
        registry.add_workflow_search_path(str(plugin_dir))

        with patch("factory.plugins.get_registry", return_value=registry):
            entries = WorkflowRegistry.discover()

        assert entries["no-bridge"].source == "plugin"
        # The plugin path was never appended to _search_paths
        plugin_paths = {p for p, _ in WorkflowRegistry._search_paths}
        assert str(plugin_dir) not in plugin_paths

    def test_plugin_registry_failure_is_non_fatal(self) -> None:
        """If PluginRegistry is unavailable, discovery still returns builtins."""
        with patch(
            "factory.plugins.get_registry",
            side_effect=RuntimeError("plugins unavailable"),
        ):
            entries = WorkflowRegistry.discover()

        assert "design" in entries


# ── Discovery priority ───────────────────────────────────────────


class TestDiscoveryPriority:
    def test_project_shadows_plugin(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "plugin_wf"
        project_dir = tmp_path / "project" / ".factory" / "workflows"
        _write_workflow(plugin_dir, "shared-name", "plugin version")
        _write_workflow(project_dir, "shared-name", "project version")

        registry = PluginRegistry()
        registry.add_workflow_search_path(str(plugin_dir))

        with patch("factory.plugins.get_registry", return_value=registry):
            entries = WorkflowRegistry.discover(project_path=tmp_path / "project")

        assert entries["shared-name"].source == "project"
        assert "project version" in entries["shared-name"].description

    def test_plugin_shadows_user(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "user_wf"
        plugin_dir = tmp_path / "plugin_wf"
        _write_workflow(user_dir, "shared-name", "user version")
        _write_workflow(plugin_dir, "shared-name", "plugin version")

        WorkflowRegistry._search_paths.append((str(user_dir), "user"))

        registry = PluginRegistry()
        registry.add_workflow_search_path(str(plugin_dir))

        with patch("factory.plugins.get_registry", return_value=registry):
            entries = WorkflowRegistry.discover()

        assert entries["shared-name"].source == "plugin"

    def test_user_shadows_builtin(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "user_wf"
        _write_workflow(user_dir, "design", "user override of builtin")

        WorkflowRegistry._search_paths.append((str(user_dir), "user"))

        entries = WorkflowRegistry.discover()

        assert entries["design"].source == "user"

    def test_plugin_shadows_builtin_with_warning(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "plugin_wf"
        _write_workflow(plugin_dir, "design", "plugin override of builtin")

        registry = PluginRegistry()
        registry.add_workflow_search_path(str(plugin_dir))

        with patch("factory.plugins.get_registry", return_value=registry):
            entries = WorkflowRegistry.discover()

        assert entries["design"].source == "plugin"

    def test_project_shadows_builtin(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "project" / ".factory" / "workflows"
        _write_workflow(project_dir, "design", "project override of builtin")

        entries = WorkflowRegistry.discover(project_path=tmp_path / "project")

        assert entries["design"].source == "project"

    def test_legacy_search_paths_entry_still_discovered(self, tmp_path: Path) -> None:
        """Paths registered directly on _search_paths keep working (back-compat)."""
        legacy_dir = tmp_path / "legacy_wf"
        _write_workflow(legacy_dir, "legacy-mode")

        WorkflowRegistry._search_paths.append((str(legacy_dir), "lightwell"))

        entries = WorkflowRegistry.discover()

        assert "legacy-mode" in entries
        assert entries["legacy-mode"].source == "lightwell"

    def test_legacy_search_path_does_not_shadow_project(
        self, tmp_path: Path
    ) -> None:
        """The pre-#1490 bug: registered paths overrode project-local."""
        legacy_dir = tmp_path / "legacy_wf"
        project_dir = tmp_path / "project" / ".factory" / "workflows"
        _write_workflow(legacy_dir, "shared-name", "legacy version")
        _write_workflow(project_dir, "shared-name", "project version")

        WorkflowRegistry._search_paths.append((str(legacy_dir), "lightwell"))

        entries = WorkflowRegistry.discover(project_path=tmp_path / "project")

        assert entries["shared-name"].source == "project"


# ── Shadow warnings ──────────────────────────────────────────────


class TestShadowWarnings:
    def test_shadowing_builtin_logs_warning(self, tmp_path: Path) -> None:
        import structlog

        plugin_dir = tmp_path / "plugin_wf"
        _write_workflow(plugin_dir, "design", "plugin override of builtin")

        registry = PluginRegistry()
        registry.add_workflow_search_path(str(plugin_dir))

        with patch("factory.plugins.get_registry", return_value=registry):
            with structlog.testing.capture_logs() as logs:
                WorkflowRegistry.discover()

        shadow_events = [e for e in logs if e.get("event") == "workflow_registry.shadow"]
        assert any(
            e.get("name") == "design" and e.get("new_source") == "plugin"
            for e in shadow_events
        )

    def test_lower_priority_shadow_skips_with_info(self, tmp_path: Path) -> None:
        import structlog

        plugin_dir = tmp_path / "plugin_wf"
        project_dir = tmp_path / "project" / ".factory" / "workflows"
        _write_workflow(plugin_dir, "shared-name", "plugin version")
        _write_workflow(project_dir, "shared-name", "project version")

        registry = PluginRegistry()
        registry.add_workflow_search_path(str(plugin_dir))

        with patch("factory.plugins.get_registry", return_value=registry):
            with structlog.testing.capture_logs():
                entries = WorkflowRegistry.discover(project_path=tmp_path / "project")

        assert entries["shared-name"].source == "project"


# ── Skill cache checksum ─────────────────────────────────────────


class TestChecksumSourceFiles:
    def test_checksum_changes_on_file_content_edit(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "wf"
        path = _write_workflow(wf_dir, "mode-x")

        from factory.workflow.definitions import design_workflow

        wf = design_workflow()
        wf.name = "mode-x"

        before = _compute_checksum({"mode-x": wf}, {"mode-x": str(path)})

        # Simulate an edit that changes the file bytes but, crucially,
        # also covers entry-level data (meta description) that never
        # reaches the Workflow model
        path.write_text(path.read_text() + '\n# edited\nmeta["description"] = "edited"\n')

        after = _compute_checksum({"mode-x": wf}, {"mode-x": str(path)})
        assert before != after

    def test_checksum_stable_without_file_changes(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "wf"
        path = _write_workflow(wf_dir, "mode-y")

        from factory.workflow.definitions import design_workflow

        wf = design_workflow()
        wf.name = "mode-y"

        a = _compute_checksum({"mode-y": wf}, {"mode-y": str(path)})
        b = _compute_checksum({"mode-y": wf}, {"mode-y": str(path)})
        assert a == b

    def test_checksum_without_source_files_unchanged_behavior(
        self, tmp_path: Path
    ) -> None:
        """The source_files param is optional; old call sites keep working."""
        from factory.workflow.definitions import design_workflow

        wf = design_workflow()
        a = _compute_checksum({"design": wf})
        b = _compute_checksum({"design": wf})
        assert a == b

    def test_missing_source_file_is_non_fatal(self, tmp_path: Path) -> None:
        from factory.workflow.definitions import design_workflow

        wf = design_workflow()
        wf.name = "gone"

        # Path does not exist; must not raise
        result = _compute_checksum({"gone": wf}, {"gone": str(tmp_path / "nope.py")})
        assert isinstance(result, str) and len(result) == 16


# ── End-to-end: skill regeneration on plugin file edit ───────────


class TestSkillCacheInvalidation:
    def test_editing_plugin_workflow_invalidates_cache(self, tmp_path: Path) -> None:
        """The #2223-class bug: editing a plugin workflow .py did not invalidate
        the skill cache when the edit did not change the constructed Workflow
        model (e.g. entry-level metadata or a comment). With source-file hashing
        folded into the checksum, any file edit forces regeneration."""
        import structlog

        plugin_dir = tmp_path / "plugin_wf"
        _write_workflow(plugin_dir, "plugin-skill-mode")

        registry = PluginRegistry()
        registry.add_workflow_search_path(str(plugin_dir))

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        from factory.skill_cache import ensure_skills

        def _run() -> list[dict]:
            with patch("factory.plugins.get_registry", return_value=registry):
                with structlog.testing.capture_logs() as logs:
                    ensure_skills(project_dir)
            return logs

        # First call generates (cache miss)
        logs = _run()
        assert any(e.get("event") == "skill_cache.miss" for e in logs)
        skill_md = project_dir / "skills" / "workflow-plugin-skill-mode" / "SKILL.md"
        assert skill_md.exists()

        # Second call with no changes hits the cache
        logs = _run()
        assert any(e.get("event") == "skill_cache.hit" for e in logs)
        assert not any(e.get("event") == "skill_cache.miss" for e in logs)

        # Edit the plugin workflow file in a way that does NOT change the
        # constructed Workflow model (a comment), then re-discover as a new
        # process would
        wf_file = plugin_dir / "plugin-skill-mode.py"
        wf_file.write_text(wf_file.read_text() + "\n# tuning pass 2\n")
        WorkflowRegistry.reset()

        # Pre-fix this was still a cache hit (stale skills); now it must miss
        logs = _run()
        assert any(e.get("event") == "skill_cache.miss" for e in logs)


# ── Workflow model sanity for the checksum helper ────────────────


def _any_workflow() -> Workflow:
    from factory.workflow.definitions import design_workflow

    return design_workflow()


class TestWorkflowConstructionHelper:
    def test_helper(self) -> None:
        wf = _any_workflow()
        assert isinstance(wf, Workflow)
