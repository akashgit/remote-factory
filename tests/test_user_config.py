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

        monkeypatch.setenv("FACTORY_RUNNER", "alt")
        config_dir.write_text('[defaults]\nrunner = "vertex"')
        result = resolve("runner", cli_value="claude", env_var="FACTORY_RUNNER",
                         config={"defaults": {"runner": "vertex"}}, default="fallback")
        assert result == "claude"

    def test_env_wins_over_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.user_config import resolve

        monkeypatch.setenv("FACTORY_RUNNER", "alt")
        result = resolve("runner", env_var="FACTORY_RUNNER",
                         config={"defaults": {"runner": "vertex"}}, default="fallback")
        assert result == "alt"

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

        monkeypatch.setenv("FACTORY_RUNNER", "alt")
        result = resolve("runner", cli_value="", env_var="FACTORY_RUNNER")
        assert result == "alt"

    def test_whitespace_cli_value_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.user_config import resolve

        monkeypatch.setenv("FACTORY_RUNNER", "alt")
        result = resolve("runner", cli_value="   ", env_var="FACTORY_RUNNER")
        assert result == "alt"


class TestLoadConfig:
    def test_returns_empty_when_no_file(self, config_dir: Path) -> None:
        from factory.user_config import load_config

        assert load_config() == {}

    def test_reads_toml(self, config_dir: Path) -> None:
        from factory.user_config import load_config

        config_dir.write_text('[defaults]\nrunner = "alt"\nmodel = "opus"')
        data = load_config()
        assert data["defaults"]["runner"] == "alt"
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
        with pytest.raises(KeyError, match="missing"):
            load_config(profile="missing")

    def test_profile_requires_file(self, config_dir: Path) -> None:
        from factory.user_config import load_config

        with pytest.raises(FileNotFoundError):
            load_config(profile="vertex")


class TestValidation:
    def test_valid_profile_name(self) -> None:
        from factory.user_config import _validate_profile_name

        _validate_profile_name("vertex-ai")
        _validate_profile_name("prod_1")
        _validate_profile_name("Prod")

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
        assert is_sensitive("SERVICE_TOKEN")
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
        from factory.user_config import ensure_config_file

        path = ensure_config_file()
        assert path.exists()
        content = path.read_text()
        assert "[defaults]" in content
        assert "[credentials." in content

    def test_secure_permissions(self, config_dir: Path) -> None:
        from factory.user_config import ensure_config_file

        path = ensure_config_file()
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_idempotent(self, config_dir: Path) -> None:
        from factory.user_config import ensure_config_file

        ensure_config_file()
        config_dir.write_text("custom content")
        ensure_config_file()
        assert config_dir.read_text() == "custom content"


class TestMigrateEnvToConfig:
    def test_migrates_env_vars(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tomli_w = pytest.importorskip("tomli_w")  # noqa: F841

        monkeypatch.setenv("FACTORY_RUNNER", "alt")
        monkeypatch.setenv("FACTORY_MODEL", "opus")
        monkeypatch.delenv("FACTORY_PROJECTS_DIR", raising=False)

        from factory.user_config import migrate_env_to_config

        msg = migrate_env_to_config()
        assert "2" in msg
        assert config_dir.exists()

        import tomllib
        with open(config_dir, "rb") as f:
            data = tomllib.load(f)
        assert data["defaults"]["runner"] == "alt"
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
            '[credentials.vertex]\nFACTORY_RUNNER = "alt"'
        )
        monkeypatch.delenv("FACTORY_RUNNER", raising=False)

        load_config(profile="vertex")
        result = resolve("runner", env_var="FACTORY_RUNNER", default="claude")
        assert result == "alt"

    def test_profile_overrides_env(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config, resolve

        config_dir.write_text('[credentials.vertex]\nFACTORY_RUNNER = "alt"')
        monkeypatch.setenv("FACTORY_RUNNER", "claude")

        load_config(profile="vertex")
        result = resolve("runner", cli_value=None, env_var="FACTORY_RUNNER", default="fallback")
        assert result == "alt"


class TestEnvOverlay:
    """Tests for profile env overlay: override, unset, protected vars."""

    def test_profile_overrides_existing_env(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config

        config_dir.write_text(
            '[credentials.test]\nFACTORY_RUNNER = "profile-value"'
        )
        monkeypatch.setenv("FACTORY_RUNNER", "original-value")
        load_config(profile="test")
        assert os.environ["FACTORY_RUNNER"] == "profile-value"

    def test_unset_removes_env_var(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config

        config_dir.write_text(
            '[credentials.test]\nFACTORY_RUNNER = "claude"\n\n'
            '[credentials.test.unset]\n'
            'vars = ["CLAUDE_CODE_USE_VERTEX"]'
        )
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        load_config(profile="test")
        assert "CLAUDE_CODE_USE_VERTEX" not in os.environ
        assert os.environ["FACTORY_RUNNER"] == "claude"

    def test_unset_missing_var_is_noop(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config

        config_dir.write_text(
            '[credentials.test]\nFACTORY_RUNNER = "claude"\n\n'
            '[credentials.test.unset]\n'
            'vars = ["NONEXISTENT_VAR_XYZ"]'
        )
        monkeypatch.delenv("NONEXISTENT_VAR_XYZ", raising=False)
        load_config(profile="test")
        assert "NONEXISTENT_VAR_XYZ" not in os.environ

    def test_protected_var_set_raises(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config

        config_dir.write_text('[credentials.bad]\nPATH = "/evil/path"')
        with pytest.raises(ValueError, match="protected variable"):
            load_config(profile="bad")

    def test_protected_var_unset_raises(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config

        config_dir.write_text(
            '[credentials.bad]\nFACTORY_RUNNER = "claude"\n\n'
            '[credentials.bad.unset]\n'
            'vars = ["HOME"]'
        )
        with pytest.raises(ValueError, match="protected variable"):
            load_config(profile="bad")

    def test_unset_subtable_not_treated_as_credential(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config

        config_dir.write_text(
            '[credentials.test]\nFACTORY_RUNNER = "claude"\n\n'
            '[credentials.test.unset]\n'
            'vars = ["SOME_VAR"]'
        )
        monkeypatch.delenv("unset", raising=False)
        load_config(profile="test")
        assert "unset" not in os.environ

    def test_unset_before_set_order(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If a var appears in both set and unset, set wins (runs second)."""
        from factory.user_config import load_config

        config_dir.write_text(
            '[credentials.test]\nMY_VAR = "set-value"\n\n'
            '[credentials.test.unset]\n'
            'vars = ["MY_VAR"]'
        )
        monkeypatch.setenv("MY_VAR", "original")
        load_config(profile="test")
        assert os.environ["MY_VAR"] == "set-value"

    def test_show_config_handles_nested_subtables(self, config_dir: Path) -> None:
        from factory.user_config import show_config

        config_dir.write_text(
            '[credentials.custom]\n'
            'FACTORY_RUNNER = "claude"\n\n'
            '[credentials.custom.unset]\n'
            'vars = ["CLAUDE_CODE_USE_VERTEX"]'
        )
        output = show_config()
        assert "[credentials.custom]" in output
        assert "claude" in output
        assert "unset" in output.lower()


class TestHardenedProtectedVars:
    """Tests for expanded protected variable list."""

    def test_protected_var_ld_preload_raises(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config

        config_dir.write_text('[credentials.bad]\nLD_PRELOAD = "/evil/lib.so"')
        with pytest.raises(ValueError, match="protected variable"):
            load_config(profile="bad")

    def test_protected_var_pythonpath_raises(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config

        config_dir.write_text('[credentials.bad]\nPYTHONPATH = "/evil"')
        with pytest.raises(ValueError, match="protected variable"):
            load_config(profile="bad")

    def test_protected_var_ifs_raises(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config

        config_dir.write_text('[credentials.bad]\nIFS = "x"')
        with pytest.raises(ValueError, match="protected variable"):
            load_config(profile="bad")

    def test_protected_var_dyld_raises(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config

        config_dir.write_text('[credentials.bad]\nDYLD_INSERT_LIBRARIES = "/evil"')
        with pytest.raises(ValueError, match="protected variable"):
            load_config(profile="bad")

    def test_protected_var_factory_trace_raises(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config

        config_dir.write_text('[credentials.bad]\nFACTORY_TRACE_ID = "injected"')
        with pytest.raises(ValueError, match="protected variable"):
            load_config(profile="bad")


class TestUnsetVarsValidation:
    """Tests for unset.vars type validation."""

    def test_unset_vars_string_not_list_raises(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.user_config import load_config

        config_dir.write_text(
            '[credentials.bad]\nFACTORY_RUNNER = "claude"\n\n'
            '[credentials.bad.unset]\n'
            'vars = "not-a-list"'
        )
        with pytest.raises(ValueError, match="must be a list"):
            load_config(profile="bad")


class TestOverrideWarning:
    """Tests for structured log warning on env var override."""

    def test_override_logs_warning(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock

        config_dir.write_text('[credentials.test]\nFACTORY_RUNNER = "new-value"')
        monkeypatch.setenv("FACTORY_RUNNER", "old-value")

        mock_log = MagicMock()
        monkeypatch.setattr("factory.user_config.log", mock_log)

        from factory.user_config import load_config
        load_config(profile="test")

        mock_log.warning.assert_any_call(
            "profile_override", key="FACTORY_RUNNER", profile="test"
        )


class TestShowConfigMasksNestedSecrets:
    """Tests for masking sensitive values in nested sub-tables."""

    def test_show_config_masks_nested_secrets(self, config_dir: Path) -> None:
        from factory.user_config import show_config

        config_dir.write_text(
            '[credentials.custom]\n'
            'FACTORY_RUNNER = "claude"\n\n'
            '[credentials.custom.secrets]\n'
            'api_key = "super-secret-key-1234"\n'
            'name = "visible"'
        )
        output = show_config()
        assert "super-secret-key-1234" not in output
        assert "1234" in output
        assert "visible" in output


class TestResolveEmptyTomlValue:
    """Cover the branch where toml_val is not None but strips to empty string."""

    def test_empty_toml_value_falls_through_to_default(self) -> None:
        from factory.user_config import resolve

        # toml_val is "" -> strip -> empty -> skip -> use default
        result = resolve("runner", config={"defaults": {"runner": ""}}, default="fallback")
        assert result == "fallback"

    def test_whitespace_toml_value_falls_through_to_default(self) -> None:
        from factory.user_config import resolve

        result = resolve("runner", config={"defaults": {"runner": "   "}}, default="fallback")
        assert result == "fallback"

    def test_none_default_when_toml_value_empty(self) -> None:
        from factory.user_config import resolve

        result = resolve("runner", config={"defaults": {"runner": ""}})
        assert result is None


class TestShowConfigCredentialsAndOtherSections:
    """Cover show_config paths: credentials sections and 'other sections'."""

    def test_show_config_masks_sensitive_in_defaults(self, config_dir: Path) -> None:
        from factory.user_config import show_config

        config_dir.write_text(
            '[defaults]\nrunner = "claude"\napi_key = "sk-secret-value-1234"'
        )
        output = show_config()
        assert "claude" in output
        # The api_key should be masked in defaults
        assert "sk-secret-value-1234" not in output
        assert "1234" in output
        assert "****" in output

    def test_show_config_reveal_shows_sensitive_in_defaults(self, config_dir: Path) -> None:
        from factory.user_config import show_config

        config_dir.write_text(
            '[defaults]\napi_key = "sk-secret-value-1234"'
        )
        output = show_config(reveal=True)
        assert "sk-secret-value-1234" in output

    def test_show_config_other_sections(self, config_dir: Path) -> None:
        from factory.user_config import show_config

        config_dir.write_text(
            '[defaults]\nrunner = "claude"\n\n'
            '[custom_section]\nfoo = "bar"\nmy_secret_key = "hidden-9999"'
        )
        output = show_config()
        # Other section should appear
        assert "[custom_section]" in output
        assert "foo = bar" in output
        # Sensitive key in other section should be masked
        assert "hidden-9999" not in output
        assert "9999" in output

    def test_show_config_other_section_reveal(self, config_dir: Path) -> None:
        from factory.user_config import show_config

        config_dir.write_text(
            '[custom_section]\nmy_secret_key = "hidden-9999"'
        )
        output = show_config(reveal=True)
        assert "hidden-9999" in output

    def test_show_config_non_dict_section_rendered(self, config_dir: Path) -> None:
        from factory.user_config import show_config

        # A top-level section that is not defaults or credentials should be rendered
        config_dir.write_text(
            '[defaults]\nrunner = "claude"\n\n'
            '[other]\nfoo = "val"'
        )
        output = show_config()
        assert "[other]" in output
        assert "foo = val" in output

    def test_show_config_multiple_credential_profiles(self, config_dir: Path) -> None:
        from factory.user_config import show_config

        config_dir.write_text(
            '[credentials.vertex]\nANTHROPIC_API_KEY = "sk-vert-1234"\n\n'
            '[credentials.staging]\nSTAGING_API_KEY = "sk-staging-5678"'
        )
        output = show_config()
        assert "[credentials.vertex]" in output
        assert "[credentials.staging]" in output
        # Both keys should be masked
        assert "sk-vert-1234" not in output
        assert "sk-staging-5678" not in output
        assert "1234" in output
        assert "5678" in output


class TestMigrateEnvToConfigMocked:
    """Cover migrate_env_to_config with mocked tomli_w (since it's not installed)."""

    def test_migrate_with_mocked_tomli_w(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys
        from unittest.mock import MagicMock

        # Mock tomli_w module
        mock_tomli_w = MagicMock()
        mock_tomli_w.dumps.return_value = '[defaults]\nrunner = "alt"\n'
        monkeypatch.setitem(sys.modules, "tomli_w", mock_tomli_w)

        # Clear all FACTORY_* env vars that migrate_env_to_config looks for
        for key in (
            "FACTORY_RUNNER", "FACTORY_MODEL", "FACTORY_PROJECTS_DIR",
            "FACTORY_VAULT_PATH", "FACTORY_PLAYBOOKS_DIR", "FACTORY_REGISTRY_DIR",
            "FACTORY_MANAGED_DIRS", "FACTORY_RUNNER_QUIET", "FACTORY_CEO_RESPAWN_DISABLED",
            "FACTORY_CEO_MAX_RESPAWNS",
        ):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("FACTORY_RUNNER", "alt")
        monkeypatch.setenv("FACTORY_MODEL", "opus")

        from factory.user_config import migrate_env_to_config

        msg = migrate_env_to_config()
        assert "Migrated 2 env var(s)" in msg
        assert config_dir.exists()

        # Verify tomli_w.dumps was called with the right structure
        call_args = mock_tomli_w.dumps.call_args[0][0]
        assert "defaults" in call_args
        assert call_args["defaults"]["runner"] == "alt"
        assert call_args["defaults"]["model"] == "opus"

    def test_migrate_no_env_vars_set(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys
        from unittest.mock import MagicMock

        mock_tomli_w = MagicMock()
        mock_tomli_w.dumps.return_value = ""
        monkeypatch.setitem(sys.modules, "tomli_w", mock_tomli_w)

        # Clear all FACTORY_ env vars
        for key in [
            "FACTORY_RUNNER", "FACTORY_MODEL", "FACTORY_PROJECTS_DIR",
            "FACTORY_VAULT_PATH", "FACTORY_PLAYBOOKS_DIR", "FACTORY_REGISTRY_DIR",
            "FACTORY_MANAGED_DIRS", "FACTORY_RUNNER_QUIET", "FACTORY_CEO_RESPAWN_DISABLED",
            "FACTORY_CEO_MAX_RESPAWNS",
        ]:
            monkeypatch.delenv(key, raising=False)

        from factory.user_config import migrate_env_to_config

        msg = migrate_env_to_config()
        assert "0" in msg

        # Should have been called with empty data (no defaults section)
        call_args = mock_tomli_w.dumps.call_args[0][0]
        assert call_args == {}

    def test_migrate_refuses_existing_file(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys
        from unittest.mock import MagicMock

        mock_tomli_w = MagicMock()
        monkeypatch.setitem(sys.modules, "tomli_w", mock_tomli_w)

        config_dir.parent.mkdir(parents=True, exist_ok=True)
        config_dir.write_text("existing")

        from factory.user_config import migrate_env_to_config

        with pytest.raises(FileExistsError, match="already exists"):
            migrate_env_to_config()

    def test_migrate_import_error_without_tomli_w(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys

        # Ensure tomli_w is NOT importable
        monkeypatch.delitem(sys.modules, "tomli_w", raising=False)

        # Mock the import to raise ImportError
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "tomli_w":
                raise ImportError("No module named 'tomli_w'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        from factory.user_config import migrate_env_to_config

        with pytest.raises(ImportError, match="tomli_w is required"):
            migrate_env_to_config()

    def test_migrate_secure_permissions(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import stat
        import sys
        from unittest.mock import MagicMock

        mock_tomli_w = MagicMock()
        mock_tomli_w.dumps.return_value = '[defaults]\nrunner = "claude"\n'
        monkeypatch.setitem(sys.modules, "tomli_w", mock_tomli_w)

        for key in (
            "FACTORY_RUNNER", "FACTORY_MODEL", "FACTORY_PROJECTS_DIR",
            "FACTORY_VAULT_PATH", "FACTORY_PLAYBOOKS_DIR", "FACTORY_REGISTRY_DIR",
            "FACTORY_MANAGED_DIRS", "FACTORY_RUNNER_QUIET", "FACTORY_CEO_RESPAWN_DISABLED",
            "FACTORY_CEO_MAX_RESPAWNS",
        ):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("FACTORY_RUNNER", "claude")

        from factory.user_config import migrate_env_to_config

        migrate_env_to_config()
        mode = stat.S_IMODE(config_dir.stat().st_mode)
        assert mode == 0o600
