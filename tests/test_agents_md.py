"""Tests for factory/runners/_agents_md.py — AGENTS.md setup/restore helpers."""

from pathlib import Path

from factory.runners._agents_md import SENTINEL, AgentsMdState, restore_agents_md, setup_agents_md


class TestSetupAgentsMd:
    def test_creates_file_with_sentinel(self, tmp_path: Path) -> None:
        state = setup_agents_md(tmp_path, "System prompt content")

        agents_path = tmp_path / "AGENTS.md"
        assert agents_path.exists()
        content = agents_path.read_text(encoding="utf-8")
        assert content.startswith(SENTINEL)
        assert "System prompt content" in content
        assert state.backup is None

        state.lock.release()

    def test_backs_up_existing_content(self, tmp_path: Path) -> None:
        agents_path = tmp_path / "AGENTS.md"
        agents_path.write_text("# My project agents\n", encoding="utf-8")

        state = setup_agents_md(tmp_path, "System prompt")

        assert state.backup == "# My project agents\n"
        content = agents_path.read_text(encoding="utf-8")
        assert content.startswith("# My project agents\n")
        assert SENTINEL in content
        assert "System prompt" in content

        state.lock.release()

    def test_stale_file_discarded(self, tmp_path: Path) -> None:
        agents_path = tmp_path / "AGENTS.md"
        agents_path.write_text(f"{SENTINEL}\nOld stale prompt\n", encoding="utf-8")

        state = setup_agents_md(tmp_path, "Fresh prompt")

        assert state.backup is None
        content = agents_path.read_text(encoding="utf-8")
        assert "Old stale prompt" not in content
        assert "Fresh prompt" in content
        assert content.startswith(SENTINEL)

        state.lock.release()

    def test_creates_lock_directory(self, tmp_path: Path) -> None:
        state = setup_agents_md(tmp_path, "prompt")
        lock_path = tmp_path / ".factory" / ".agents_md.lock"
        assert lock_path.parent.exists()
        state.lock.release()


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

    def test_restore_releases_lock(self, tmp_path: Path) -> None:
        state = setup_agents_md(tmp_path, "prompt")
        assert state.lock.is_locked

        restore_agents_md(state)

        assert not state.lock.is_locked

    def test_restore_releases_lock_on_os_error(self, tmp_path: Path) -> None:
        agents_path = tmp_path / "AGENTS.md"
        agents_path.write_text("# Original\n", encoding="utf-8")
        state = setup_agents_md(tmp_path, "prompt")

        agents_path.unlink()
        subdir = tmp_path / "AGENTS.md"
        subdir.mkdir()

        restore_agents_md(state)

        assert not state.lock.is_locked


class TestLocking:
    def test_lock_prevents_concurrent_setup(self, tmp_path: Path) -> None:
        state1 = setup_agents_md(tmp_path, "First prompt")

        lock_path = tmp_path / ".factory" / ".agents_md.lock"
        from filelock import FileLock, Timeout
        import pytest

        lock2 = FileLock(lock_path, timeout=0.1)
        with pytest.raises(Timeout):
            lock2.acquire()

        restore_agents_md(state1)

        lock2.acquire()
        lock2.release()
