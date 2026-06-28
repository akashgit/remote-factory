"""E2E tests for tmux CLI integration — requires real tmux."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

_TEST_PREFIX = "test-factory-"


def _tmux_available() -> bool:
    try:
        subprocess.run(["tmux", "-V"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _kill_test_sessions() -> None:
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        for name in result.stdout.strip().splitlines():
            if name.startswith(_TEST_PREFIX):
                subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not _tmux_available(), reason="tmux not available"),
]


class TestTmuxSessionNameCollision:
    def test_different_paths_same_basename(self, tmp_path: Path) -> None:
        from factory.cli import _tmux_session_name

        p1 = tmp_path / "a" / "myapp"
        p2 = tmp_path / "b" / "myapp"
        p1.mkdir(parents=True)
        p2.mkdir(parents=True)

        name1 = _tmux_session_name(p1)
        name2 = _tmux_session_name(p2)
        assert name1 != name2

        s1 = f"{_TEST_PREFIX}{name1}"
        s2 = f"{_TEST_PREFIX}{name2}"
        try:
            subprocess.run(
                ["tmux", "new-session", "-d", "-s", s1, "sleep 30"],
                check=True,
            )
            subprocess.run(
                ["tmux", "new-session", "-d", "-s", s2, "sleep 30"],
                check=True,
            )

            result = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_name}"],
                capture_output=True, text=True,
            )
            sessions = result.stdout.strip().splitlines()
            assert s1 in sessions
            assert s2 in sessions
        finally:
            _kill_test_sessions()


class TestTmuxLsE2E:
    def test_tmux_ls_shows_sessions(self, tmp_path: Path) -> None:
        session = f"{_TEST_PREFIX}ls-test"
        try:
            subprocess.run(
                ["tmux", "new-session", "-d", "-s", session, "sleep 30"],
                check=True,
            )

            result = subprocess.run(
                [sys.executable, "-m", "factory.cli", "tmux-ls"],
                capture_output=True, text=True,
                cwd=str(tmp_path),
            )

            assert result.returncode == 0
        finally:
            _kill_test_sessions()


class TestTmuxStopE2E:
    def test_tmux_stop_kills_specific_session(self, tmp_path: Path) -> None:
        session = f"{_TEST_PREFIX}stop-test"
        try:
            subprocess.run(
                ["tmux", "new-session", "-d", "-s", session, "sleep 30"],
                check=True,
            )

            check = subprocess.run(
                ["tmux", "has-session", "-t", session],
                capture_output=True,
            )
            assert check.returncode == 0

            result = subprocess.run(
                [sys.executable, "-m", "factory.cli", "tmux-stop", "--session", session],
                capture_output=True, text=True,
                cwd=str(tmp_path),
            )
            assert result.returncode == 0

            time.sleep(0.5)
            check2 = subprocess.run(
                ["tmux", "has-session", "-t", session],
                capture_output=True,
            )
            assert check2.returncode != 0
        finally:
            _kill_test_sessions()
