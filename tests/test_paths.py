"""Tests for factory.paths — canonical user-level path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestFactoryHome:
    def test_default_is_home_dot_factory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FACTORY_HOME", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        from factory.paths import factory_home

        assert factory_home() == tmp_path / ".factory"

    def test_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        custom = tmp_path / "custom-factory"
        custom.mkdir()
        monkeypatch.setenv("FACTORY_HOME", str(custom))
        from factory.paths import factory_home

        assert factory_home() == custom

    def test_env_override_expands_tilde(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_HOME", "~/my-factory")
        from factory.paths import factory_home

        result = factory_home()
        assert "~" not in str(result)

    def test_empty_env_uses_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_HOME", "")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        from factory.paths import factory_home

        assert factory_home() == tmp_path / ".factory"

    def test_whitespace_env_uses_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_HOME", "   ")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        from factory.paths import factory_home

        assert factory_home() == tmp_path / ".factory"


class TestSubPaths:
    def test_registry_under_factory_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_HOME", str(tmp_path))
        from factory.paths import registry_path

        assert registry_path() == tmp_path / "registry.json"

    def test_config_under_factory_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_HOME", str(tmp_path))
        from factory.paths import config_path

        assert config_path() == tmp_path / "config.toml"

    def test_playbooks_under_factory_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_HOME", str(tmp_path))
        from factory.paths import playbooks_dir

        assert playbooks_dir() == tmp_path / "playbooks"

    def test_profile_under_factory_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_HOME", str(tmp_path))
        from factory.paths import profile_path

        assert profile_path() == tmp_path / "profile.md"

    def test_all_paths_consistent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_HOME", str(tmp_path))
        from factory.paths import config_path, factory_home, playbooks_dir, profile_path, registry_path

        home = factory_home()
        for sub in [registry_path(), config_path(), playbooks_dir(), profile_path()]:
            assert str(sub).startswith(str(home)), f"{sub} not under {home}"
