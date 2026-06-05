"""Tests for factory.md parsing of the ## Build Root section."""

from pathlib import Path

from factory.store import ExperimentStore


class TestBuildRootParsing:
    async def test_parses_build_root_section(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / ".factory").mkdir()
        (project / "factory.md").write_text(
            "## Goal\nBuild a build root\n\n"
            "## Build Root\n"
            "- project_repo: https://github.com/spring-projects/spring-framework\n"
            "- version_tag: v5.2.9\n"
            "- jdk_version: 11\n"
        )
        store = ExperimentStore(project)
        config = await store.reparse_config()
        assert config.build_root is not None
        assert config.build_root.project_repo == "https://github.com/spring-projects/spring-framework"
        assert config.build_root.version_tag == "v5.2.9"
        assert config.build_root.jdk_version == 11

    async def test_build_root_defaults(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / ".factory").mkdir()
        (project / "factory.md").write_text(
            "## Goal\nBuild root\n\n"
            "## Build Root\n"
            "- project_repo: https://example.com/repo\n"
            "- version_tag: v1.0\n"
        )
        store = ExperimentStore(project)
        config = await store.reparse_config()
        assert config.build_root is not None
        assert config.build_root.jdk_version == 11
        assert config.build_root.build_system == "gradle"
        assert config.build_root.known_fixes_path == "config/known-fixes.yaml"
        assert config.build_root.local_repo_path == "local-repo/"

    async def test_missing_required_fields_returns_none(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / ".factory").mkdir()
        (project / "factory.md").write_text(
            "## Goal\nTest\n\n"
            "## Build Root\n"
            "- jdk_version: 11\n"
        )
        store = ExperimentStore(project)
        config = await store.reparse_config()
        assert config.build_root is None
