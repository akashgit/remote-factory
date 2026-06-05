"""Tests for BuildRootConfig Pydantic model and its integration with FactoryConfig/CycleState."""

from datetime import datetime

import pytest

from factory.models import BuildRootConfig, CycleState, FactoryConfig


class TestBuildRootConfig:
    def test_valid_config(self):
        br = BuildRootConfig(
            project_repo="https://github.com/spring-projects/spring-framework",
            version_tag="v5.2.9",
        )
        assert br.project_repo == "https://github.com/spring-projects/spring-framework"
        assert br.version_tag == "v5.2.9"
        assert br.jdk_version == 11
        assert br.build_system == "gradle"
        assert br.known_fixes_path == "config/known-fixes.yaml"
        assert br.local_repo_path == "local-repo/"

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            BuildRootConfig(
                project_repo="x",
                version_tag="v1",
                extra="bad",
            )

    def test_roundtrip_json(self):
        br = BuildRootConfig(
            project_repo="https://example.com/repo",
            version_tag="v1.0",
            jdk_version=17,
        )
        data = br.model_dump()
        restored = BuildRootConfig(**data)
        assert restored == br

    def test_custom_jdk(self):
        br = BuildRootConfig(
            project_repo="x",
            version_tag="v1",
            jdk_version=17,
        )
        assert br.jdk_version == 17

    def test_factory_config_with_build_root(self):
        br = BuildRootConfig(project_repo="x", version_tag="v1")
        config = FactoryConfig(
            goal="Build root test",
            scope=[],
            guards=[],
            eval_command="./build.sh",
            eval_threshold=0.0,
            constraints=[],
            build_root=br,
        )
        assert config.build_root is not None
        assert config.build_root.version_tag == "v1"
        assert config.build_root.project_repo == "x"

    def test_factory_config_defaults_none(self):
        config = FactoryConfig(
            goal="Test",
            scope=[],
            guards=[],
            eval_command="pytest",
            eval_threshold=0.8,
            constraints=[],
        )
        assert config.build_root is None

    def test_cycle_state_build_root_mode(self):
        cs = CycleState(
            cycle_id="test-123",
            started_at=datetime.now(),
            mode="build-root",
        )
        assert cs.mode == "build-root"
