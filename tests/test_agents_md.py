"""Tests for factory/runners/_agents_md.py — AGENTS.md setup/restore helpers."""

from pathlib import Path

from factory.runners._agents_md import SENTINEL, restore_agents_md, setup_agents_md


class TestSetupAgentsMd:
    def test_creates_file_with_sentinel(self, tmp_path: Path) -> None:
        state = setup_agents_md(tmp_path, "System prompt content")

        agents_path = tmp_path / "AGENTS.md"
        assert agents_path.exists()
        content = agents_path.read_text(encoding="utf-8")
        assert content.startswith(SENTINEL)
        assert "System prompt content" in content
        assert state.backup is None

    def test_backs_up_existing_content(self, tmp_path: Path) -> None:
        agents_path = tmp_path / "AGENTS.md"
        agents_path.write_text("# My project agents\n", encoding="utf-8")

        state = setup_agents_md(tmp_path, "System prompt")

        assert state.backup == "# My project agents\n"
        content = agents_path.read_text(encoding="utf-8")
        assert content.startswith("# My project agents\n")
        assert SENTINEL in content
        assert "System prompt" in content

    def test_stale_file_discarded(self, tmp_path: Path) -> None:
        agents_path = tmp_path / "AGENTS.md"
        agents_path.write_text(f"{SENTINEL}\nOld stale prompt\n", encoding="utf-8")

        state = setup_agents_md(tmp_path, "Fresh prompt")

        assert state.backup is None
        content = agents_path.read_text(encoding="utf-8")
        assert "Old stale prompt" not in content
        assert "Fresh prompt" in content
        assert content.startswith(SENTINEL)


class TestRestoreAgentsMd:
    def test_restore_removes_file_when_no_backup(self, tmp_path: Path) -> None:
        state = setup_agents_md(tmp_path, "System prompt")
        agents_path = tmp_path / "AGENTS.md"
        assert agents_path.exists()

        restore_agents_md(state)

        assert not agents_path.exists()

    def test_restore_writes_backup(self, tmp_path: Path) -> None:
        agents_path = tmp_path / "AGENTS.md"
        agents_path.write_text("# Original\n", encoding="utf-8")

        state = setup_agents_md(tmp_path, "System prompt")
        assert SENTINEL in agents_path.read_text(encoding="utf-8")

        restore_agents_md(state)

        assert agents_path.read_text(encoding="utf-8") == "# Original\n"

    def test_restore_none_is_noop(self) -> None:
        restore_agents_md(None)

    def test_restore_handles_os_error(self, tmp_path: Path) -> None:
        agents_path = tmp_path / "AGENTS.md"
        agents_path.write_text("# Original\n", encoding="utf-8")
        state = setup_agents_md(tmp_path, "prompt")

        agents_path.unlink()
        subdir = tmp_path / "AGENTS.md"
        subdir.mkdir()

        restore_agents_md(state)
