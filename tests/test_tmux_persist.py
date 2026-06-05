"""Tests for the tmux persist module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from factory.runners._tmux_persist import (
    _strip_ansi,
    run_in_tmux,
    tmux_available,
)
from factory.runners.claude import ClaudeRunner


class TestTmuxAvailable:
    def test_returns_true_when_tmux_found(self) -> None:
        with patch("factory.runners._tmux_persist.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert tmux_available() is True
            mock_run.assert_called_once_with(["tmux", "-V"], capture_output=True, check=True)

    def test_returns_false_when_tmux_not_found(self) -> None:
        with patch("factory.runners._tmux_persist.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError
            assert tmux_available() is False

    def test_returns_false_when_tmux_fails(self) -> None:
        import subprocess

        with patch("factory.runners._tmux_persist.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "tmux")
            assert tmux_available() is False


class TestStripAnsi:
    def test_strips_color_codes(self) -> None:
        assert _strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_strips_cursor_movement(self) -> None:
        assert _strip_ansi("\x1b[2Jhello\x1b[1A") == "hello"

    def test_preserves_plain_text(self) -> None:
        assert _strip_ansi("hello world") == "hello world"

    def test_handles_empty_string(self) -> None:
        assert _strip_ansi("") == ""

    def test_strips_osc_title_sequences(self) -> None:
        assert _strip_ansi("\x1b]0;Window Title\x07hello") == "hello"

    def test_strips_dec_private_mode(self) -> None:
        assert _strip_ansi("\x1b[?25lhidden cursor\x1b[?25h") == "hidden cursor"

    def test_strips_save_restore_cursor(self) -> None:
        assert _strip_ansi("\x1b7saved\x1b8") == "saved"


class TestRunInTmux:
    async def test_creates_new_session_when_none_exists(self, tmp_path: Path) -> None:
        project_path = tmp_path / "my-project"
        project_path.mkdir()
        (project_path / ".factory").mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist.asyncio.create_subprocess_exec") as mock_async,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1),  # has-session
                MagicMock(returncode=0),  # new-session
            ]
            wait_proc = AsyncMock()
            wait_proc.wait = AsyncMock(return_value=0)
            mock_async.return_value = wait_proc

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("agent output here")
                (tmpdir / "exitcode").write_text("0")

                stdout, code, _ = await run_in_tmux(
                    "system prompt", "do task", project_path, "researcher", project_path,
                )

            assert code == 0
            assert "agent output here" in stdout

            new_session_call = mock_run.call_args_list[1]
            cmd = new_session_call[0][0]
            assert "new-session" in cmd

    async def test_creates_window_when_session_exists(self, tmp_path: Path) -> None:
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist.asyncio.create_subprocess_exec") as mock_async,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # has-session (exists)
                MagicMock(returncode=0),  # new-window
            ]
            wait_proc = AsyncMock()
            wait_proc.wait = AsyncMock(return_value=0)
            mock_async.return_value = wait_proc

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("output")
                (tmpdir / "exitcode").write_text("0")

                await run_in_tmux(
                    "prompt", "task", project_path, "builder", project_path,
                )

            new_window_call = mock_run.call_args_list[1]
            cmd = new_window_call[0][0]
            assert "new-window" in cmd

    async def test_tmux_command_references_wrapper_script(self, tmp_path: Path) -> None:
        """Verify the tmux new-session command references the wrapper script path."""
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist.asyncio.create_subprocess_exec") as mock_async,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1),
                MagicMock(returncode=0),
            ]
            wait_proc = AsyncMock()
            wait_proc.wait = AsyncMock(return_value=0)
            mock_async.return_value = wait_proc

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("")
                (tmpdir / "exitcode").write_text("0")

                await run_in_tmux(
                    "test prompt", "test task", project_path, "researcher", project_path,
                    model="sonnet",
                )

            # The tmux new-session command should reference the wrapper script
            new_session_call = mock_run.call_args_list[1]
            cmd = new_session_call[0][0]
            assert any("wrapper.sh" in str(arg) for arg in cmd)

    async def test_returns_error_on_tmux_window_failure(self, tmp_path: Path) -> None:
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        with patch("factory.runners._tmux_persist.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1),  # has-session
                MagicMock(returncode=1, stderr=b"error"),  # new-session fails
            ]

            stdout, code, _ = await run_in_tmux(
                "prompt", "task", project_path, "builder", project_path,
            )

            assert code == 1
            assert "Failed" in stdout

    async def test_timeout_kills_tmux_window(self, tmp_path: Path) -> None:
        import asyncio

        project_path = tmp_path / "my-project"
        project_path.mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist.asyncio.create_subprocess_exec") as mock_async,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1),  # has-session
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0),  # kill-window
            ]
            wait_proc = AsyncMock()
            wait_proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError, 0])
            wait_proc.kill = MagicMock()
            mock_async.return_value = wait_proc

            stdout, code, _ = await run_in_tmux(
                "prompt", "task", project_path, "builder", project_path,
                timeout=1.0,
            )

            assert code == 1
            assert "timed out" in stdout

            kill_call = mock_run.call_args_list[2]
            cmd = kill_call[0][0]
            assert "kill-window" in cmd

    async def test_strips_ansi_from_output(self, tmp_path: Path) -> None:
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist.asyncio.create_subprocess_exec") as mock_async,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1),
                MagicMock(returncode=0),
            ]
            wait_proc = AsyncMock()
            wait_proc.wait = AsyncMock(return_value=0)
            mock_async.return_value = wait_proc

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("\x1b[32mgreen text\x1b[0m")
                (tmpdir / "exitcode").write_text("0")

                stdout, code, _ = await run_in_tmux(
                    "prompt", "task", project_path, "researcher", project_path,
                )

            assert stdout == "green text"
            assert "\x1b" not in stdout


class TestClaudeRunnerTmuxPersist:
    async def test_headless_delegates_to_run_in_tmux(self, tmp_path: Path) -> None:
        from factory.models import AgentRunRequest

        runner = ClaudeRunner()
        (tmp_path / ".factory").mkdir()

        request = AgentRunRequest(
            prompt="test prompt", task="test task", cwd=tmp_path,
            role="researcher", extras={"tmux_persist": True},
        )

        with (
            patch("factory.runners._tmux_persist.tmux_available", return_value=True),
            patch("factory.runners._tmux_persist.run_in_tmux", new_callable=AsyncMock, return_value=("tmux output", 0, None)) as mock_run,
        ):
            result = await runner.headless(request)

            assert result.stdout == "tmux output"
            assert result.return_code == 0
            assert result.usage is None
            mock_run.assert_called_once()

    async def test_headless_falls_back_when_tmux_unavailable(self, tmp_path: Path) -> None:
        from factory.models import AgentRunRequest, AgentRunResult

        runner = ClaudeRunner()

        request = AgentRunRequest(
            prompt="test prompt", task="test task", cwd=tmp_path,
            extras={"tmux_persist": True},
        )

        mock_result = AgentRunResult(stdout="headless output", return_code=0)

        with (
            patch("factory.runners._tmux_persist.tmux_available", return_value=False),
            patch("factory.runners.claude.run_subprocess", new_callable=AsyncMock, return_value=mock_result),
        ):
            await runner.headless(request)

    async def test_headless_skips_tmux_when_not_requested(self, tmp_path: Path) -> None:
        from factory.models import AgentRunRequest, AgentRunResult

        runner = ClaudeRunner()

        request = AgentRunRequest(
            prompt="test prompt", task="test task", cwd=tmp_path,
            extras={"tmux_persist": False},
        )

        mock_result = AgentRunResult(stdout="normal", return_code=0)

        with (
            patch("factory.runners.claude.run_subprocess", new_callable=AsyncMock, return_value=mock_result),
        ):
            await runner.headless(request)
