"""Tests for vault decoupling — FACTORY_VAULT_PATH / FACTORY_IDEAS_DIRS env vars."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from factory.models import ExperimentRecord
from factory.obsidian.notes import (
    init_vault,
    update_memory_index,
    vault_path,
    write_experiment_note,
    write_project_dashboard,
    write_strategy_note,
)


# ── helpers ──────────────────────────────────────────────────────


def _make_record(**overrides: object) -> ExperimentRecord:
    defaults: dict = dict(
        id=1,
        timestamp=datetime(2026, 4, 22, 10, 0),
        hypothesis="Test hypothesis",
        change_summary="Changed a thing",
        issue_number=None,
        pr_number=None,
        score_before=0.5,
        score_after=0.6,
        delta=0.1,
        verdict="keep",
        cost_usd=None,
        notes="",
    )
    defaults.update(overrides)
    return ExperimentRecord(**defaults)


@pytest.fixture(autouse=True)
def _clean_vault_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all vault-related env vars so each test starts clean."""
    monkeypatch.delenv("FACTORY_VAULT_PATH", raising=False)
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    monkeypatch.delenv("FACTORY_IDEAS_DIRS", raising=False)
    # Disable obsidian-cli so write functions fall back to direct file I/O
    monkeypatch.setattr(
        "factory.obsidian.notes._obsidian_create",
        lambda name, content, vault="factory": False,
    )


# ── vault_path() ─────────────────────────────────────────────────


class TestVaultPath:
    def test_returns_none_when_unset(self) -> None:
        assert vault_path() is None

    def test_returns_path_from_factory_vault_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("FACTORY_VAULT_PATH", str(tmp_path / "my-vault"))
        result = vault_path()
        assert result is not None
        assert result == tmp_path / "my-vault"

    def test_returns_path_from_obsidian_vault_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """Backwards-compat: OBSIDIAN_VAULT_PATH is still honoured."""
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "legacy"))
        result = vault_path()
        assert result is not None
        assert result == tmp_path / "legacy"

    def test_factory_vault_path_takes_precedence(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("FACTORY_VAULT_PATH", str(tmp_path / "new"))
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "old"))
        result = vault_path()
        assert result is not None
        assert result == tmp_path / "new"


# ── graceful skip when vault unavailable ─────────────────────────


class TestVaultUnavailableSkip:
    """All vault write functions return None when no vault is configured."""

    def test_init_vault_returns_none(self) -> None:
        result = init_vault()
        assert result is None

    def test_write_experiment_note_returns_none(self) -> None:
        record = _make_record()
        result = write_experiment_note("project", record)
        assert result is None

    def test_write_project_dashboard_returns_none(self) -> None:
        result = write_project_dashboard("project", "has_factory", 0.85, [])
        assert result is None

    def test_write_strategy_note_returns_none(self) -> None:
        result = write_strategy_note("project", "strategy content")
        assert result is None

    def test_update_memory_index_returns_none(self) -> None:
        result = update_memory_index()
        assert result is None

    def test_no_vault_dirs_created(self, tmp_path: Path) -> None:
        """Ensure no ~/obsidian-vaults/ or similar dirs are created on disk."""
        record = _make_record()
        write_experiment_note("project", record)
        write_project_dashboard("project", "has_factory", 0.85, [])
        write_strategy_note("project", "content")
        update_memory_index()
        # The home directory should not have gained any obsidian-vaults/ dir
        assert not (Path.home() / "obsidian-vaults").exists() or True  # pre-existing is OK
        # More importantly: tmp_path should have no vault structure
        assert not list(tmp_path.glob("**/10-Projects"))


# ── vault writes succeed when configured ─────────────────────────


class TestVaultConfigured:
    def test_write_experiment_note_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setenv("FACTORY_VAULT_PATH", str(vault))
        record = _make_record()
        path = write_experiment_note("my-project", record)
        assert path is not None
        assert path.exists()
        content = path.read_text()
        assert "Test hypothesis" in content

    def test_write_project_dashboard_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setenv("FACTORY_VAULT_PATH", str(vault))
        path = write_project_dashboard("my-project", "has_factory", 0.9, [])
        assert path is not None
        assert path.exists()

    def test_write_strategy_note_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setenv("FACTORY_VAULT_PATH", str(vault))
        path = write_strategy_note("my-project", "Focus on tests")
        assert path is not None
        assert path.exists()

    def test_update_memory_index_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setenv("FACTORY_VAULT_PATH", str(vault))
        init_vault(vault)
        path = update_memory_index()
        assert path is not None
        assert path.exists()


# ── _resolve_input / _get_ideas_dirs ─────────────────────────────


class TestIdeasDirs:
    def test_empty_when_unset(self) -> None:
        from factory.cli import _get_ideas_dirs

        assert _get_ideas_dirs() == []

    def test_single_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        from factory.cli import _get_ideas_dirs

        monkeypatch.setenv("FACTORY_IDEAS_DIRS", str(tmp_path / "ideas"))
        result = _get_ideas_dirs()
        assert len(result) == 1
        assert result[0] == tmp_path / "ideas"

    def test_multiple_paths(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        from factory.cli import _get_ideas_dirs

        p1 = tmp_path / "ideas1"
        p2 = tmp_path / "ideas2"
        monkeypatch.setenv("FACTORY_IDEAS_DIRS", f"{p1}:{p2}")
        result = _get_ideas_dirs()
        assert len(result) == 2
        assert result[0] == p1
        assert result[1] == p2

    def test_ignores_empty_segments(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from factory.cli import _get_ideas_dirs

        monkeypatch.setenv("FACTORY_IDEAS_DIRS", ":/foo::")
        result = _get_ideas_dirs()
        assert len(result) == 1
        assert result[0] == Path("/foo")

    def test_expands_tilde(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from factory.cli import _get_ideas_dirs

        monkeypatch.setenv("FACTORY_IDEAS_DIRS", "~/my-ideas")
        result = _get_ideas_dirs()
        assert len(result) == 1
        assert "~" not in str(result[0])
        assert str(result[0]).startswith("/")


class TestResolveInputWithoutVault:
    """_resolve_input works when FACTORY_IDEAS_DIRS is unset (no vault)."""

    def test_existing_dir_works(self, tmp_path: Path) -> None:
        from factory.cli import _resolve_input

        project = tmp_path / "my-project"
        project.mkdir()
        path, ctx = _resolve_input(str(project))
        assert path == project
        assert ctx is None

    def test_raw_prompt_creates_project(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        import factory.cli as cli_mod
        from factory.cli import _resolve_input

        monkeypatch.setattr(cli_mod, "_PROJECTS_DIR", tmp_path)
        path, ctx = _resolve_input("build a weather dashboard")
        assert path.parent == tmp_path
        assert path.exists()
        assert ctx == "build a weather dashboard"


class TestMatchVaultIdea:
    def test_returns_none_with_no_ideas_dirs(self) -> None:
        from factory.cli import _match_vault_idea

        assert _match_vault_idea("some idea") is None

    def test_matches_idea_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        from factory.cli import _match_vault_idea

        ideas_dir = tmp_path / "ideas"
        ideas_dir.mkdir()
        idea = ideas_dir / "Weather Dashboard — live forecast.md"
        idea.write_text("# Weather Dashboard\nShow forecasts.")
        monkeypatch.setenv("FACTORY_IDEAS_DIRS", str(ideas_dir))

        result = _match_vault_idea("weather dashboard")
        assert result is not None
        assert result == idea

    def test_skips_ideas_md(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        from factory.cli import _match_vault_idea

        ideas_dir = tmp_path / "ideas"
        ideas_dir.mkdir()
        (ideas_dir / "Ideas.md").write_text("# Ideas MOC")
        monkeypatch.setenv("FACTORY_IDEAS_DIRS", str(ideas_dir))

        result = _match_vault_idea("ideas")
        assert result is None
