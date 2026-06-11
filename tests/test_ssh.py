"""Tests for factory.ssh — SSH agent connectivity checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from factory.cli import _TMUX_PROPAGATE_VARS
from factory.ssh import check_ssh_agent


def _git_remote_output(urls: list[str]) -> str:
    lines = []
    for i, url in enumerate(urls):
        name = "origin" if i == 0 else f"remote{i}"
        lines.append(f"{name}\t{url} (fetch)")
        lines.append(f"{name}\t{url} (push)")
    return "\n".join(lines)


class TestCheckSSHAgent:
    """Test check_ssh_agent() across the four documented scenarios."""

    def test_ssh_available_and_ssh_remote_no_warning(self, tmp_path: Path) -> None:
        """SSH agent running + SSH remote → no warning."""
        sock = tmp_path / "agent.sock"
        sock.touch()

        with (
            patch.dict("os.environ", {"SSH_AUTH_SOCK": str(sock)}, clear=False),
            patch("factory.ssh.subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = _git_remote_output(["git@github.com:user/repo.git"])

            result = check_ssh_agent(tmp_path)

        assert result.has_ssh_remotes is True
        assert result.agent_socket_set is True
        assert result.agent_socket_exists is True
        assert result.needs_warning is False

    def test_ssh_missing_and_ssh_remote_warns(self, tmp_path: Path) -> None:
        """No SSH_AUTH_SOCK + SSH remote → warning emitted."""
        env = {k: v for k, v in __import__("os").environ.items() if k != "SSH_AUTH_SOCK"}
        with (
            patch.dict("os.environ", env, clear=True),
            patch("factory.ssh.subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = _git_remote_output(["git@github.com:user/repo.git"])

            result = check_ssh_agent(tmp_path)

        assert result.has_ssh_remotes is True
        assert result.agent_socket_set is False
        assert result.agent_socket_exists is False
        assert result.needs_warning is True

    def test_ssh_available_and_https_remote_no_warning(self, tmp_path: Path) -> None:
        """SSH agent running + HTTPS remote → no warning."""
        sock = tmp_path / "agent.sock"
        sock.touch()

        with (
            patch.dict("os.environ", {"SSH_AUTH_SOCK": str(sock)}, clear=False),
            patch("factory.ssh.subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = _git_remote_output(["https://github.com/user/repo.git"])

            result = check_ssh_agent(tmp_path)

        assert result.has_ssh_remotes is False
        assert result.needs_warning is False

    def test_stale_socket_path_warns(self, tmp_path: Path) -> None:
        """SSH_AUTH_SOCK set but file missing → warning emitted."""
        stale_path = tmp_path / "gone.sock"

        with (
            patch.dict("os.environ", {"SSH_AUTH_SOCK": str(stale_path)}, clear=False),
            patch("factory.ssh.subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = _git_remote_output(["ssh://git@github.com/user/repo.git"])

            result = check_ssh_agent(tmp_path)

        assert result.has_ssh_remotes is True
        assert result.agent_socket_set is True
        assert result.agent_socket_exists is False
        assert result.needs_warning is True

    def test_no_git_remotes_no_warning(self, tmp_path: Path) -> None:
        """No remotes at all → no warning regardless of SSH state."""
        env = {k: v for k, v in __import__("os").environ.items() if k != "SSH_AUTH_SOCK"}
        with (
            patch.dict("os.environ", env, clear=True),
            patch("factory.ssh.subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""

            result = check_ssh_agent(tmp_path)

        assert result.has_ssh_remotes is False
        assert result.needs_warning is False


class TestTmuxPropagateVars:
    """Verify _TMUX_PROPAGATE_VARS includes required SSH variables."""

    def test_includes_ssh_auth_sock(self) -> None:
        assert "SSH_AUTH_SOCK" in _TMUX_PROPAGATE_VARS

    def test_includes_ssh_agent_pid(self) -> None:
        assert "SSH_AGENT_PID" in _TMUX_PROPAGATE_VARS

    def test_includes_cloud_vars(self) -> None:
        assert "CLAUDE_CODE_USE_VERTEX" in _TMUX_PROPAGATE_VARS
        assert "CLOUD_ML_REGION" in _TMUX_PROPAGATE_VARS
        assert "ANTHROPIC_VERTEX_PROJECT_ID" in _TMUX_PROPAGATE_VARS

    def test_includes_runner_vars(self) -> None:
        assert "FACTORY_RUNNER" in _TMUX_PROPAGATE_VARS
        assert "FACTORY_MODEL" in _TMUX_PROPAGATE_VARS
        assert "BOBSHELL_API_KEY" in _TMUX_PROPAGATE_VARS
        assert "CODEX_API_KEY" in _TMUX_PROPAGATE_VARS
        assert "OPENAI_API_KEY" in _TMUX_PROPAGATE_VARS
        assert "ANTHROPIC_API_KEY" in _TMUX_PROPAGATE_VARS
