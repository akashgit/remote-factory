"""Tests for factory.ace.paths — playbook path resolution."""

from __future__ import annotations

from pathlib import Path


class TestUserPlaybooksDir:
    def test_creates_directory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FACTORY_PLAYBOOKS_DIR", str(tmp_path / "custom"))
        from factory.ace.paths import user_playbooks_dir

        result = user_playbooks_dir()
        assert result == tmp_path / "custom"
        assert result.is_dir()

    def test_env_var_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FACTORY_PLAYBOOKS_DIR", str(tmp_path / "override"))
        from factory.ace.paths import user_playbooks_dir

        assert user_playbooks_dir() == tmp_path / "override"

    def test_default_is_home_factory(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FACTORY_PLAYBOOKS_DIR", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        from factory.ace.paths import user_playbooks_dir

        result = user_playbooks_dir()
        assert result == tmp_path / ".factory" / "playbooks"


class TestResolvePlaybookPath:
    def test_prefers_user_local(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FACTORY_PLAYBOOKS_DIR", str(tmp_path / "user"))
        from factory.ace.paths import DEFAULTS_DIR, resolve_playbook_path

        user_dir = tmp_path / "user"
        user_dir.mkdir(parents=True)
        (user_dir / "ceo.md").write_text("user version")

        default = DEFAULTS_DIR / "ceo.md"
        assert default.exists(), "factory default must exist for this test"

        result = resolve_playbook_path("ceo")
        assert result == user_dir / "ceo.md"

    def test_falls_back_to_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FACTORY_PLAYBOOKS_DIR", str(tmp_path / "empty"))
        from factory.ace.paths import DEFAULTS_DIR, resolve_playbook_path

        (tmp_path / "empty").mkdir()
        result = resolve_playbook_path("ceo")
        assert result == DEFAULTS_DIR / "ceo.md"

    def test_returns_none_for_unknown_role(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FACTORY_PLAYBOOKS_DIR", str(tmp_path / "empty"))
        from factory.ace.paths import resolve_playbook_path

        (tmp_path / "empty").mkdir()
        assert resolve_playbook_path("nonexistent_role_xyz") is None


class TestSeedUserPlaybooks:
    def test_copies_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FACTORY_PLAYBOOKS_DIR", str(tmp_path / "user"))
        from factory.ace.paths import DEFAULTS_DIR, seed_user_playbooks

        seed_user_playbooks()
        user_dir = tmp_path / "user"
        for default in DEFAULTS_DIR.glob("*.md"):
            user_file = user_dir / default.name
            assert user_file.exists(), f"{default.name} not seeded"

    def test_does_not_overwrite_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FACTORY_PLAYBOOKS_DIR", str(tmp_path / "user"))
        from factory.ace.paths import seed_user_playbooks

        user_dir = tmp_path / "user"
        user_dir.mkdir(parents=True)
        (user_dir / "ceo.md").write_text("my evolved playbook")

        seed_user_playbooks()
        assert (user_dir / "ceo.md").read_text() == "my evolved playbook"
