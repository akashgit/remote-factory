"""Tests for the tmux persist module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from factory.runners._tmux_persist import (
    _generate_settings,
    _strip_ansi,
    _wait_for_sentinel,
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


class TestGenerateSettings:
    def test_creates_settings_json(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "sentinel"
        settings_file = _generate_settings(sentinel, tmp_path)
        assert settings_file.exists()
        data = json.loads(settings_file.read_text())
        assert "hooks" in data
        assert "Stop" in data["hooks"]
        assert "StopFailure" in data["hooks"]

    def test_hooks_touch_sentinel_path(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "sentinel"
        settings_file = _generate_settings(sentinel, tmp_path)
        data = json.loads(settings_file.read_text())
        stop_cmd = data["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert f"touch {sentinel}" in stop_cmd
        fail_cmd = data["hooks"]["StopFailure"][0]["hooks"][0]["command"]
        assert f"touch {sentinel}" in fail_cmd

    def test_hook_structure(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "sentinel"
        settings_file = _generate_settings(sentinel, tmp_path)
        data = json.loads(settings_file.read_text())
        hook = data["hooks"]["Stop"][0]["hooks"][0]
        assert hook["type"] == "command"
        assert hook["timeout"] == 5


class TestWaitForSentinel:
    async def test_returns_true_when_sentinel_exists(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "sentinel"
        sentinel.touch()
        result = await _wait_for_sentinel(sentinel, timeout=5.0)
        assert result is True

    async def test_returns_false_on_timeout(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "sentinel"
        with patch("factory.runners._tmux_persist._SENTINEL_POLL_INTERVAL", 0.01):
            result = await _wait_for_sentinel(sentinel, timeout=0.03)
        assert result is False

    async def test_detects_sentinel_created_mid_poll(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "sentinel"
        call_count = 0
        original_sleep = __import__("asyncio").sleep

        async def mock_sleep(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                sentinel.touch()
            await original_sleep(0)

        with (
            patch("factory.runners._tmux_persist.asyncio.sleep", side_effect=mock_sleep),
            patch("factory.runners._tmux_persist._SENTINEL_POLL_INTERVAL", 0.01),
        ):
            result = await _wait_for_sentinel(sentinel, timeout=10.0)
        assert result is True


class TestRunInTmux:
    async def test_creates_new_session_when_none_exists(self, tmp_path: Path) -> None:
        project_path = tmp_path / "my-project"
        project_path.mkdir()
        (project_path / ".factory").mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._wait_for_sentinel", new_callable=AsyncMock, return_value=True),
            patch("factory.runners._tmux_persist.asyncio.sleep", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._session_exists", side_effect=[False, False]),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0),  # send-keys /exit
            ]

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

            new_session_call = mock_run.call_args_list[0]
            cmd = new_session_call[0][0]
            assert "new-session" in cmd

    async def test_creates_window_when_session_exists(self, tmp_path: Path) -> None:
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._wait_for_sentinel", new_callable=AsyncMock, return_value=True),
            patch("factory.runners._tmux_persist.asyncio.sleep", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._session_exists", side_effect=[True, False]),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # new-window
                MagicMock(returncode=0),  # send-keys /exit
            ]

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("output")
                (tmpdir / "exitcode").write_text("0")

                await run_in_tmux(
                    "prompt", "task", project_path, "builder", project_path,
                )

            new_window_call = mock_run.call_args_list[0]
            cmd = new_window_call[0][0]
            assert "new-window" in cmd

    async def test_wrapper_script_includes_settings_and_trap(self, tmp_path: Path) -> None:
        """Verify the wrapper script has --settings flag and trap EXIT."""
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        captured_wrapper = {}

        original_write_text = Path.write_text

        def spy_write_text(self_path: Path, content: str, *args, **kwargs) -> None:
            if self_path.name == "wrapper.sh":
                captured_wrapper["content"] = content
            original_write_text(self_path, content, *args, **kwargs)

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._wait_for_sentinel", new_callable=AsyncMock, return_value=True),
            patch("factory.runners._tmux_persist.asyncio.sleep", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._session_exists", side_effect=[False, False]),
            patch.object(Path, "write_text", spy_write_text),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0),  # send-keys /exit
            ]

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("agent output")
                (tmpdir / "exitcode").write_text("0")

                await run_in_tmux(
                    "test prompt", "test task", project_path, "researcher", project_path,
                    model="sonnet",
                )

        assert "content" in captured_wrapper
        content = captured_wrapper["content"]
        assert "trap cleanup EXIT" in content
        assert "--settings" in content

    async def test_claude_command_includes_settings_flag(self, tmp_path: Path) -> None:
        """Verify the claude command includes --settings pointing to settings.json."""
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        captured_wrapper = {}
        original_write_text = Path.write_text

        def spy_write_text(self_path: Path, content: str, *args, **kwargs) -> None:
            if self_path.name == "wrapper.sh":
                captured_wrapper["content"] = content
            original_write_text(self_path, content, *args, **kwargs)

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._wait_for_sentinel", new_callable=AsyncMock, return_value=True),
            patch("factory.runners._tmux_persist.asyncio.sleep", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._session_exists", side_effect=[False, False]),
            patch.object(Path, "write_text", spy_write_text),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0),  # send-keys /exit
            ]

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("output")
                (tmpdir / "exitcode").write_text("0")

                await run_in_tmux(
                    "prompt", "task", project_path, "builder", project_path,
                )

        assert "content" in captured_wrapper
        content = captured_wrapper["content"]
        assert "--settings" in content
        assert "settings.json" in content

    async def test_returns_error_on_tmux_window_failure(self, tmp_path: Path) -> None:
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._session_exists", return_value=False),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1, stderr=b"error"),  # new-session fails
            ]

            stdout, code, _ = await run_in_tmux(
                "prompt", "task", project_path, "builder", project_path,
            )

            assert code == 1
            assert "Failed" in stdout

    async def test_timeout_kills_tmux_window(self, tmp_path: Path) -> None:
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._wait_for_sentinel", new_callable=AsyncMock, return_value=False),
            patch("factory.runners._tmux_persist._session_exists", return_value=False),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0),  # kill-window (timeout)
            ]

            stdout, code, _ = await run_in_tmux(
                "prompt", "task", project_path, "builder", project_path,
                timeout=1.0,
            )

            assert code == 1
            assert "timed out" in stdout

            kill_call = mock_run.call_args_list[1]
            cmd = kill_call[0][0]
            assert "kill-window" in cmd

    async def test_strips_ansi_from_output(self, tmp_path: Path) -> None:
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._wait_for_sentinel", new_callable=AsyncMock, return_value=True),
            patch("factory.runners._tmux_persist.asyncio.sleep", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._session_exists", side_effect=[False, False]),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0),  # send-keys /exit
            ]

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

    async def test_sends_exit_after_sentinel(self, tmp_path: Path) -> None:
        """After sentinel detection, /exit is sent to the tmux pane."""
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._wait_for_sentinel", new_callable=AsyncMock, return_value=True),
            patch("factory.runners._tmux_persist.asyncio.sleep", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._session_exists", side_effect=[False, False]),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0),  # send-keys /exit
            ]

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("output")
                (tmpdir / "exitcode").write_text("0")

                await run_in_tmux(
                    "prompt", "task", project_path, "builder", project_path,
                )

            send_keys_call = mock_run.call_args_list[1]
            cmd = send_keys_call[0][0]
            assert "send-keys" in cmd
            assert "/exit" in cmd

    async def test_fallback_kill_window_when_session_still_alive(self, tmp_path: Path) -> None:
        """After /exit + sleep, if session still exists, kill-window is called."""
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._wait_for_sentinel", new_callable=AsyncMock, return_value=True),
            patch("factory.runners._tmux_persist.asyncio.sleep", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._session_exists", side_effect=[False, True]),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0),  # send-keys /exit
                MagicMock(returncode=0),  # kill-window fallback
            ]

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("output")
                (tmpdir / "exitcode").write_text("0")

                stdout, code, _ = await run_in_tmux(
                    "prompt", "task", project_path, "builder", project_path,
                )

            assert code == 0
            kill_call = mock_run.call_args_list[2]
            cmd = kill_call[0][0]
            assert "kill-window" in cmd


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
