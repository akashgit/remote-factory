"""Tests for factory.profile — named Claude instance profile management."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.profile import (
    apply_profile,
    create_profile,
    delete_profile,
    format_profile,
    list_profiles,
    load_profile,
)


@pytest.fixture()
def profiles_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect PROFILES_DIR to a temp directory for each test."""
    d = tmp_path / "profiles"
    monkeypatch.setattr("factory.profile.PROFILES_DIR", d)
    return d


class TestCreateAndLoad:
    def test_create_roundtrip(self, profiles_dir: Path) -> None:
        env = {"ANTHROPIC_API_KEY": "sk-ant-test", "ANTHROPIC_BASE_URL": "https://example.com"}
        path = create_profile("work", env)
        assert path.exists()
        loaded = load_profile("work")
        assert loaded == env

    def test_create_overwrites(self, profiles_dir: Path) -> None:
        create_profile("work", {"KEY": "old"})
        create_profile("work", {"KEY": "new"})
        assert load_profile("work") == {"KEY": "new"}

    def test_load_missing_raises(self, profiles_dir: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Profile 'missing' not found"):
            load_profile("missing")

    def test_load_missing_lists_available(self, profiles_dir: Path) -> None:
        create_profile("personal", {"KEY": "v"})
        with pytest.raises(FileNotFoundError, match="personal"):
            load_profile("other")

    def test_load_malformed_json_raises(self, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        (profiles_dir / "bad.json").write_text("not json")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_profile("bad")

    def test_load_bad_env_type_raises(self, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        (profiles_dir / "bad.json").write_text(json.dumps({"env": "string"}))
        with pytest.raises(ValueError, match="'env' must be a dict"):
            load_profile("bad")

    def test_create_creates_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        d = tmp_path / "deep" / "nested" / "profiles"
        monkeypatch.setattr("factory.profile.PROFILES_DIR", d)
        create_profile("p", {"K": "V"})
        assert d.exists()


class TestApplyProfile:
    def test_apply_sets_environ(self, profiles_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        create_profile("work", {"ANTHROPIC_API_KEY": "sk-ant-work"})
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        applied = apply_profile("work")
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-work"
        assert applied == {"ANTHROPIC_API_KEY": "sk-ant-work"}

    def test_apply_overrides_existing(self, profiles_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "old-key")
        create_profile("new", {"ANTHROPIC_API_KEY": "new-key"})
        apply_profile("new")
        assert os.environ["ANTHROPIC_API_KEY"] == "new-key"

    def test_apply_missing_raises(self, profiles_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            apply_profile("nonexistent")


class TestListProfiles:
    def test_list_empty(self, profiles_dir: Path) -> None:
        assert list_profiles() == []

    def test_list_sorted(self, profiles_dir: Path) -> None:
        create_profile("work", {"K": "v"})
        create_profile("personal", {"K": "v"})
        create_profile("staging", {"K": "v"})
        assert list_profiles() == ["personal", "staging", "work"]

    def test_list_ignores_non_json(self, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        (profiles_dir / "not-a-profile.txt").write_text("nope")
        create_profile("real", {"K": "v"})
        assert list_profiles() == ["real"]


class TestDeleteProfile:
    def test_delete_removes_file(self, profiles_dir: Path) -> None:
        create_profile("temp", {"K": "v"})
        delete_profile("temp")
        assert list_profiles() == []

    def test_delete_missing_raises(self, profiles_dir: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Profile 'ghost' not found"):
            delete_profile("ghost")


class TestFormatProfile:
    def test_format_masks_secrets(self, profiles_dir: Path) -> None:
        create_profile("work", {"ANTHROPIC_API_KEY": "sk-ant-supersecret1234"})
        output = format_profile("work")
        assert "sk-ant-supersecret1234" not in output  # full value hidden
        assert "supersecret" not in output  # middle masked

    def test_format_reveals_secrets(self, profiles_dir: Path) -> None:
        create_profile("work", {"ANTHROPIC_API_KEY": "sk-ant-supersecret1234"})
        output = format_profile("work", reveal=True)
        assert "sk-ant-supersecret1234" in output

    def test_format_non_secret_shown_plainly(self, profiles_dir: Path) -> None:
        create_profile("work", {"ANTHROPIC_BASE_URL": "https://example.com"})
        output = format_profile("work")
        assert "https://example.com" in output


class TestCLIProfileCommand:
    """Test factory profile subcommands via main()."""

    def test_profile_create_and_list(self, profiles_dir: Path) -> None:
        from factory.cli import main

        with patch("factory.profile.PROFILES_DIR", profiles_dir):
            rc = main(["profile", "create", "work", "ANTHROPIC_API_KEY=sk-ant-test"])
        assert rc == 0
        assert (profiles_dir / "work.json").exists()

    def test_profile_list_empty(self, profiles_dir: Path, capsys: pytest.CaptureFixture) -> None:
        from factory.cli import main

        with patch("factory.profile.PROFILES_DIR", profiles_dir):
            rc = main(["profile", "list"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "No profiles found" in out

    def test_profile_list_shows_names(self, profiles_dir: Path, capsys: pytest.CaptureFixture) -> None:
        from factory.cli import main

        create_profile("work", {"K": "v"})
        with patch("factory.profile.PROFILES_DIR", profiles_dir):
            rc = main(["profile", "list"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "work" in out

    def test_profile_show(self, profiles_dir: Path, capsys: pytest.CaptureFixture) -> None:
        from factory.cli import main

        create_profile("work", {"ANTHROPIC_BASE_URL": "https://example.com"})
        with patch("factory.profile.PROFILES_DIR", profiles_dir):
            rc = main(["profile", "show", "work"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "https://example.com" in out

    def test_profile_show_missing_fails(self, profiles_dir: Path, capsys: pytest.CaptureFixture) -> None:
        from factory.cli import main

        with patch("factory.profile.PROFILES_DIR", profiles_dir):
            rc = main(["profile", "show", "nonexistent"])
        assert rc == 1

    def test_profile_delete(self, profiles_dir: Path) -> None:
        from factory.cli import main

        create_profile("temp", {"K": "v"})
        with patch("factory.profile.PROFILES_DIR", profiles_dir):
            rc = main(["profile", "delete", "temp"])
        assert rc == 0
        assert not (profiles_dir / "temp.json").exists()

    def test_profile_apply_prints_exports(self, profiles_dir: Path, capsys: pytest.CaptureFixture) -> None:
        from factory.cli import main

        create_profile("work", {"ANTHROPIC_API_KEY": "sk-ant-test"})
        with patch("factory.profile.PROFILES_DIR", profiles_dir):
            rc = main(["profile", "apply", "work"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "export ANTHROPIC_API_KEY=" in out
        assert "sk-ant-test" in out

    def test_profile_create_bad_pair_fails(self, profiles_dir: Path, capsys: pytest.CaptureFixture) -> None:
        from factory.cli import main

        with patch("factory.profile.PROFILES_DIR", profiles_dir):
            rc = main(["profile", "create", "bad", "NOKEYVALUE"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "KEY=VALUE" in err

    def test_ceo_profile_flag_parsed(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["ceo", "/some/path", "--profile", "work"])
        assert args.profile == "work"

    def test_run_profile_flag_parsed(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["run", "/some/path", "--profile", "personal"])
        assert args.profile == "personal"

    def test_agent_profile_flag_parsed(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["agent", "researcher", "--task", "t", "--project", "/p", "--profile", "work"])
        assert args.profile == "work"
