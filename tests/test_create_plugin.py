"""Tests for plugin package generation in CREATE mode."""

from pathlib import Path

from factory.cli._task_builder import _build_ceo_task, _slug


class TestSlug:
    def test_basic(self):
        assert _slug("approval workflow") == "approval-workflow"

    def test_special_chars(self):
        assert _slug("My Cool Mode!!! v2") == "my-cool-mode-v2"

    def test_truncation(self):
        result = _slug("a" * 60)
        assert len(result) <= 40

    def test_strips_leading_trailing_hyphens(self):
        assert _slug("---hello---") == "hello"


class TestPluginTaskBuilder:
    def test_plugin_mode_produces_plugin_header(self, tmp_path: Path):
        task = _build_ceo_task(
            tmp_path, "create",
            create_description="approval workflow",
            plugin_mode=True,
        )
        assert "## Create Mode (Plugin Package)" in task
        assert "plugin_mode:" in task
        assert "pyproject.toml" in task
        assert "register_plugin" in task

    def test_plugin_mode_with_folder(self, tmp_path: Path):
        task = _build_ceo_task(
            tmp_path, "create",
            create_description="approval workflow",
            plugin_mode=True,
            plugin_folder="/tmp/my-plugin",
        )
        assert "/tmp/my-plugin" in task
        assert "## Create Mode (Plugin Package)" in task

    def test_plugin_mode_default_folder(self, tmp_path: Path):
        task = _build_ceo_task(
            tmp_path, "create",
            create_description="approval workflow",
            plugin_mode=True,
        )
        assert "approval-workflow-plugin" in task

    def test_non_plugin_create_unchanged(self, tmp_path: Path):
        task = _build_ceo_task(
            tmp_path, "create",
            create_description="approval workflow",
            plugin_mode=False,
        )
        assert "## Create Mode (New Factory Mode)" in task
        assert "## Create Mode (Plugin Package)" not in task

    def test_update_mode_takes_precedence_over_plugin(self, tmp_path: Path):
        task = _build_ceo_task(
            tmp_path, "create",
            create_description="add plugin support",
            update_existing_mode="create",
            plugin_mode=True,
        )
        assert "## Create Mode (Update Existing Mode)" in task
        assert "## Create Mode (Plugin Package)" not in task
