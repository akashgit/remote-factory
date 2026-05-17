"""Tests for factory.user_config — config.toml loading, precedence, masking, validation."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


@pytest.fixture()
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect CONFIG_PATH to a temp directory and clear cached config."""
    cfg = tmp_path / "config.toml"
    monkeypatch.setattr("factory.user_config.CONFIG_PATH", cfg)
    monkeypatch.setattr("factory.user_config._cached_config", None)
    return cfg


class TestResolve:
    def test_cli_wins_over_all(self, config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.user_config import resolve

        monkeypatch.setenv("FACTORY_RUNNER", "bob")
        config_dir.write_text('[defaults]\nrunner = "vertex"')
        result = resolve("runner", cli_value="claude", env_var="FACTORY_RUNNER",
                         config={"defaults": {"runner": "vertex"}}, default="fallback")
        assert result == "claude"

    def test_env_wins_over_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.user_config import resolve

        monkeypatch.setenv("FACTORY_RUNNER", "bob")
        result = resolve("runner", env_var="FACTORY_RUNNER",
                         config={"defaults": {"runner": "vertex"}}, default="fallback")
        assert result == "bob"

    def test_config_wins_over_default(self) -> None:
        from factory.user_config import resolve

        result = resolve("runner", config={"defaults": {"runner": "vertex"}}, default="claude")
        assert result == "vertex"

    def test_auto_loads_config_file(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import resolve

        config_dir.write_text('[defaults]\nrunner = "from-toml"')
        monkeypatch.delenv("FACTORY_RUNNER", raising=False)
        result = resolve("runner", env_var="FACTORY_RUNNER", default="fallback")
        assert result == "from-toml"

    def test_default_used_when_nothing_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.user_config import resolve

        monkeypatch.delenv("FACTORY_RUNNER", raising=False)
        result = resolve("runner", env_var="FACTORY_RUNNER", default="claude")
        assert result == "claude"

    def test_none_when_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.user_config import resolve

        monkeypatch.delenv("FACTORY_RUNNER", raising=False)
        result = resolve("runner", env_var="FACTORY_RUNNER")
        assert result is None

    def test_empty_cli_value_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.user_config import resolve

        monkeypatch.setenv("FACTORY_RUNNER", "bob")
        result = resolve("runner", cli_value="", env_var="FACTORY_RUNNER")
        assert result == "bob"

    def test_whitespace_cli_value_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.user_config import resolve

        monkeypatch.setenv("FACTORY_RUNNER", "bob")
        result = resolve("runner", cli_value="   ", env_var="FACTORY_RUNNER")
        assert result == "bob"


class TestLoadConfig:
    def test_returns_empty_when_no_file(self, config_dir: Path) -> None:
        from factory.user_config import load_config

        assert load_config() == {}

    def test_reads_toml(self, config_dir: Path) -> None:
        from factory.user_config import load_config

        config_dir.write_text('[defaults]\nrunner = "bob"\nmodel = "opus"')
        data = load_config()
        assert data["defaults"]["runner"] == "bob"
        assert data["defaults"]["model"] == "opus"

    def test_profile_injects_env_vars(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config

        config_dir.write_text(
            '[credentials.vertex]\nFACTORY_RUNNER = "claude"\n'
            'ANTHROPIC_API_KEY = "sk-test-123"'
        )
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        load_config(profile="vertex")
        assert os.environ["FACTORY_RUNNER"] == "claude"
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-test-123"

    def test_profile_not_found_raises(self, config_dir: Path) -> None:
        from factory.user_config import load_config

        config_dir.write_text('[credentials.vertex]\nFACTORY_RUNNER = "claude"')
        with pytest.raises(KeyError, match="bob"):
            load_config(profile="bob")

    def test_profile_requires_file(self, config_dir: Path) -> None:
        from factory.user_config import load_config

        with pytest.raises(FileNotFoundError):
            load_config(profile="vertex")


class TestValidation:
    def test_valid_profile_name(self) -> None:
        from factory.user_config import _validate_profile_name

        _validate_profile_name("vertex-ai")
        _validate_profile_name("prod_1")
        _validate_profile_name("Bob")

    def test_invalid_profile_name_raises(self) -> None:
        from factory.user_config import _validate_profile_name

        with pytest.raises(ValueError, match="Invalid profile name"):
            _validate_profile_name("../../etc/passwd")

    def test_invalid_profile_name_spaces(self) -> None:
        from factory.user_config import _validate_profile_name

        with pytest.raises(ValueError, match="Invalid profile name"):
            _validate_profile_name("has space")

    def test_valid_credential_keys(self) -> None:
        from factory.user_config import _validate_credential_keys

        _validate_credential_keys({"FACTORY_RUNNER": "claude", "API_KEY": "x"})

    def test_invalid_credential_key_raises(self) -> None:
        from factory.user_config import _validate_credential_keys

        with pytest.raises(ValueError, match="Invalid credential key"):
            _validate_credential_keys({"lower_case": "bad"})

    def test_invalid_credential_key_starts_with_digit(self) -> None:
        from factory.user_config import _validate_credential_keys

        with pytest.raises(ValueError, match="Invalid credential key"):
            _validate_credential_keys({"1BAD": "val"})


class TestMasking:
    def test_is_sensitive(self) -> None:
        from factory.user_config import is_sensitive

        assert is_sensitive("ANTHROPIC_API_KEY")
        assert is_sensitive("api_key")
        assert is_sensitive("secret")
        assert is_sensitive("password")
        assert is_sensitive("BOB_TOKEN")
        assert not is_sensitive("runner")
        assert not is_sensitive("model")
        assert not is_sensitive("projects_dir")

    def test_mask_value_long(self) -> None:
        from factory.user_config import mask_value

        assert mask_value("sk-ant-abcdefgh") == "***********efgh"

    def test_mask_value_short(self) -> None:
        from factory.user_config import mask_value

        assert mask_value("abc") == "****"

    def test_show_config_masks_secrets(self, config_dir: Path) -> None:
        from factory.user_config import show_config

        config_dir.write_text(
            '[defaults]\nrunner = "claude"\n\n'
            '[credentials.vertex]\nANTHROPIC_API_KEY = "sk-ant-super-secret-1234"'
        )
        output = show_config()
        assert "claude" in output
        assert "sk-ant-super-secret-1234" not in output
        assert "1234" in output
        assert "****" in output

    def test_show_config_reveal(self, config_dir: Path) -> None:
        from factory.user_config import show_config

        config_dir.write_text(
            '[credentials.vertex]\nANTHROPIC_API_KEY = "sk-ant-super-secret-1234"'
        )
        output = show_config(reveal=True)
        assert "sk-ant-super-secret-1234" in output

    def test_show_config_no_file(self, config_dir: Path) -> None:
        from factory.user_config import show_config

        output = show_config()
        assert "No config file" in output


class TestEnsureConfigFile:
    def test_creates_with_template(self, config_dir: Path) -> None:
        from factory.user_config import _ensure_config_file

        path = _ensure_config_file()
        assert path.exists()
        content = path.read_text()
        assert "[defaults]" in content
        assert "[credentials." in content

    def test_secure_permissions(self, config_dir: Path) -> None:
        from factory.user_config import _ensure_config_file

        path = _ensure_config_file()
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_idempotent(self, config_dir: Path) -> None:
        from factory.user_config import _ensure_config_file

        _ensure_config_file()
        config_dir.write_text("custom content")
        _ensure_config_file()
        assert config_dir.read_text() == "custom content"


class TestMigrateEnvToConfig:
    def test_migrates_env_vars(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tomli_w = pytest.importorskip("tomli_w")  # noqa: F841

        monkeypatch.setenv("FACTORY_RUNNER", "bob")
        monkeypatch.setenv("FACTORY_MODEL", "opus")
        monkeypatch.delenv("FACTORY_PROJECTS_DIR", raising=False)

        from factory.user_config import migrate_env_to_config

        msg = migrate_env_to_config()
        assert "2" in msg
        assert config_dir.exists()

        import tomllib
        with open(config_dir, "rb") as f:
            data = tomllib.load(f)
        assert data["defaults"]["runner"] == "bob"
        assert data["defaults"]["model"] == "opus"

    def test_refuses_if_file_exists(self, config_dir: Path) -> None:
        pytest.importorskip("tomli_w")
        config_dir.parent.mkdir(parents=True, exist_ok=True)
        config_dir.write_text("existing")

        from factory.user_config import migrate_env_to_config

        with pytest.raises(FileExistsError):
            migrate_env_to_config()

    def test_secure_permissions_on_migrate(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pytest.importorskip("tomli_w")
        monkeypatch.setenv("FACTORY_RUNNER", "claude")

        from factory.user_config import migrate_env_to_config

        migrate_env_to_config()
        mode = stat.S_IMODE(config_dir.stat().st_mode)
        assert mode == 0o600


class TestProfilePrecedence:
    """End-to-end: profile credentials are available via resolve()."""

    def test_profile_then_resolve(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config, resolve

        config_dir.write_text(
            '[defaults]\nrunner = "claude"\n\n'
            '[credentials.vertex]\nFACTORY_RUNNER = "bob"'
        )
        monkeypatch.delenv("FACTORY_RUNNER", raising=False)

        load_config(profile="vertex")
        result = resolve("runner", env_var="FACTORY_RUNNER", default="claude")
        assert result == "bob"

    def test_env_overrides_profile(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config, resolve

        config_dir.write_text('[credentials.vertex]\nFACTORY_RUNNER = "bob"')
        monkeypatch.setenv("FACTORY_RUNNER", "claude")

        load_config(profile="vertex")
        result = resolve("runner", cli_value=None, env_var="FACTORY_RUNNER", default="fallback")
        assert result == "claude"
