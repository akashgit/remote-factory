"""Tests for the tmux persist module."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from factory.runners._tmux_persist import (
    _generate_settings,
    _strip_ansi,
    _wait_for_exitcode,
    _wait_for_sentinel,
    _window_exists,
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


class TestWindowExists:
    def test_returns_true_when_window_alive(self) -> None:
        with patch("factory.runners._tmux_persist.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert _window_exists("mysession", "mywindow") is True
            mock_run.assert_called_once_with(
                ["tmux", "has-session", "-t", "mysession:mywindow"],
                capture_output=True,
            )

    def test_returns_false_when_window_dead(self) -> None:
        with patch("factory.runners._tmux_persist.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert _window_exists("mysession", "mywindow") is False


class TestGenerateSettings:
    def test_creates_settings_json(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "sentinel"
        project = tmp_path / "proj"
        project.mkdir()
        settings_file = _generate_settings(sentinel, tmp_path, project)
        assert settings_file.exists()
        data = json.loads(settings_file.read_text())
        assert "hooks" in data
        assert "Stop" in data["hooks"]
        assert "StopFailure" in data["hooks"]

    def test_hooks_touch_sentinel_path(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "sentinel"
        project = tmp_path / "proj"
        project.mkdir()
        settings_file = _generate_settings(sentinel, tmp_path, project)
        data = json.loads(settings_file.read_text())
        stop_cmd = data["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert f"touch {sentinel}" in stop_cmd
        fail_cmd = data["hooks"]["StopFailure"][0]["hooks"][0]["command"]
        assert f"touch {sentinel}" in fail_cmd

    def test_hook_structure(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "sentinel"
        project = tmp_path / "proj"
        project.mkdir()
        settings_file = _generate_settings(sentinel, tmp_path, project)
        data = json.loads(settings_file.read_text())
        hook = data["hooks"]["Stop"][0]["hooks"][0]
        assert hook["type"] == "command"
        assert hook["timeout"] == 5

    def test_merges_existing_project_settings(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "sentinel"
        project = tmp_path / "proj"
        project.mkdir()
        claude_dir = project / ".claude"
        claude_dir.mkdir()
        existing = {
            "hooks": {
                "PreToolUse": [{"hooks": [{"type": "command", "command": "echo pre", "timeout": 5}]}],
                "Stop": [{"hooks": [{"type": "command", "command": "echo existing-stop", "timeout": 5}]}],
            },
            "permissions": {"allow": ["Read"]},
        }
        (claude_dir / "settings.json").write_text(json.dumps(existing))

        settings_file = _generate_settings(sentinel, tmp_path, project)
        data = json.loads(settings_file.read_text())

        assert "PreToolUse" in data["hooks"]
        assert data["permissions"] == {"allow": ["Read"]}
        assert len(data["hooks"]["Stop"]) == 2
        assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo existing-stop"
        assert "touch" in data["hooks"]["Stop"][1]["hooks"][0]["command"]

    def test_handles_missing_project_settings(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "sentinel"
        project = tmp_path / "proj"
        project.mkdir()
        settings_file = _generate_settings(sentinel, tmp_path, project)
        data = json.loads(settings_file.read_text())
        assert len(data["hooks"]["Stop"]) == 1
        assert len(data["hooks"]["StopFailure"]) == 1


class TestWaitForSentinel:
    async def test_returns_true_when_sentinel_exists(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "sentinel"
        sentinel.touch()
        result = await _wait_for_sentinel(sentinel, timeout=5.0)
        assert result is True

    async def test_returns_false_on_timeout(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "sentinel"
        with patch("factory.runners._tmux_persist._SENTINEL_POLL_INITIAL", 0.01):
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
            patch("factory.runners._tmux_persist._SENTINEL_POLL_INITIAL", 0.01),
        ):
            result = await _wait_for_sentinel(sentinel, timeout=10.0)
        assert result is True

    async def test_uses_exponential_backoff(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "sentinel"
        sleep_intervals: list[float] = []
        original_sleep = __import__("asyncio").sleep
        call_count = 0

        async def mock_sleep(seconds: float) -> None:
            nonlocal call_count
            sleep_intervals.append(seconds)
            call_count += 1
            if call_count >= 5:
                sentinel.touch()
            await original_sleep(0)

        with (
            patch("factory.runners._tmux_persist.asyncio.sleep", side_effect=mock_sleep),
            patch("factory.runners._tmux_persist._SENTINEL_POLL_INITIAL", 0.1),
            patch("factory.runners._tmux_persist._SENTINEL_POLL_CAP", 2.0),
        ):
            await _wait_for_sentinel(sentinel, timeout=60.0)

        assert len(sleep_intervals) >= 3
        assert sleep_intervals[0] <= 0.1 + 0.01
        assert sleep_intervals[1] <= 0.2 + 0.01
        assert sleep_intervals[2] <= 0.4 + 0.01


class TestWaitForExitcode:
    async def test_returns_exitcode_when_file_exists(self, tmp_path: Path) -> None:
        exitcode_file = tmp_path / "exitcode"
        exitcode_file.write_text("0")
        result = await _wait_for_exitcode(exitcode_file)
        assert result == 0

    async def test_returns_1_on_timeout(self, tmp_path: Path) -> None:
        exitcode_file = tmp_path / "exitcode"
        with (
            patch("factory.runners._tmux_persist._EXITCODE_POLL_TIMEOUT", 0.05),
            patch("factory.runners._tmux_persist._EXITCODE_POLL_INTERVAL", 0.01),
        ):
            result = await _wait_for_exitcode(exitcode_file)
        assert result == 1

    async def test_waits_for_delayed_exitcode(self, tmp_path: Path) -> None:
        exitcode_file = tmp_path / "exitcode"
        call_count = 0
        original_sleep = __import__("asyncio").sleep

        async def mock_sleep(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                exitcode_file.write_text("42")
            await original_sleep(0)

        with (
            patch("factory.runners._tmux_persist.asyncio.sleep", side_effect=mock_sleep),
            patch("factory.runners._tmux_persist._EXITCODE_POLL_TIMEOUT", 10.0),
        ):
            result = await _wait_for_exitcode(exitcode_file)
        assert result == 42


class TestRunInTmux:
    async def test_creates_new_session_when_none_exists(self, tmp_path: Path) -> None:
        project_path = tmp_path / "my-project"
        project_path.mkdir()
        (project_path / ".factory").mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._wait_for_sentinel", new_callable=AsyncMock, return_value=True),
            patch("factory.runners._tmux_persist._wait_for_window_exit", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._wait_for_exitcode", new_callable=AsyncMock, return_value=0),
            patch("factory.runners._tmux_persist._session_exists", return_value=False),
            patch("factory.runners._tmux_persist._window_exists", return_value=False),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0),  # send-keys /exit
            ]

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("agent output here")

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
            patch("factory.runners._tmux_persist._wait_for_window_exit", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._wait_for_exitcode", new_callable=AsyncMock, return_value=0),
            patch("factory.runners._tmux_persist._session_exists", return_value=True),
            patch("factory.runners._tmux_persist._window_exists", return_value=False),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # new-window
                MagicMock(returncode=0),  # send-keys /exit
            ]

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("output")

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
            patch("factory.runners._tmux_persist._wait_for_window_exit", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._wait_for_exitcode", new_callable=AsyncMock, return_value=0),
            patch("factory.runners._tmux_persist._session_exists", return_value=False),
            patch("factory.runners._tmux_persist._window_exists", return_value=False),
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
            patch("factory.runners._tmux_persist._wait_for_window_exit", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._wait_for_exitcode", new_callable=AsyncMock, return_value=0),
            patch("factory.runners._tmux_persist._session_exists", return_value=False),
            patch("factory.runners._tmux_persist._window_exists", return_value=False),
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
            patch("factory.runners._tmux_persist._wait_for_window_exit", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._wait_for_exitcode", new_callable=AsyncMock, return_value=0),
            patch("factory.runners._tmux_persist._session_exists", return_value=False),
            patch("factory.runners._tmux_persist._window_exists", return_value=False),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0),  # send-keys /exit
            ]

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("\x1b[32mgreen text\x1b[0m")

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
            patch("factory.runners._tmux_persist._wait_for_window_exit", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._wait_for_exitcode", new_callable=AsyncMock, return_value=0),
            patch("factory.runners._tmux_persist._session_exists", return_value=False),
            patch("factory.runners._tmux_persist._window_exists", return_value=False),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0),  # send-keys /exit
            ]

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("output")

                await run_in_tmux(
                    "prompt", "task", project_path, "builder", project_path,
                )

            send_keys_call = mock_run.call_args_list[1]
            cmd = send_keys_call[0][0]
            assert "send-keys" in cmd
            assert "/exit" in cmd

    async def test_fallback_kill_window_when_window_still_alive(self, tmp_path: Path) -> None:
        """After /exit + poll, if window still exists, kill-window is called."""
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._wait_for_sentinel", new_callable=AsyncMock, return_value=True),
            patch("factory.runners._tmux_persist._wait_for_window_exit", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._wait_for_exitcode", new_callable=AsyncMock, return_value=0),
            patch("factory.runners._tmux_persist._session_exists", return_value=False),
            patch("factory.runners._tmux_persist._window_exists", return_value=True),
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

                stdout, code, _ = await run_in_tmux(
                    "prompt", "task", project_path, "builder", project_path,
                )

            assert code == 0
            kill_call = mock_run.call_args_list[2]
            cmd = kill_call[0][0]
            assert "kill-window" in cmd

    async def test_tmux_command_references_wrapper_script(self, tmp_path: Path) -> None:
        """Verify the tmux new-session/new-window command references the wrapper script path."""
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._wait_for_sentinel", new_callable=AsyncMock, return_value=True),
            patch("factory.runners._tmux_persist._wait_for_window_exit", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._wait_for_exitcode", new_callable=AsyncMock, return_value=0),
            patch("factory.runners._tmux_persist._session_exists", return_value=False),
            patch("factory.runners._tmux_persist._window_exists", return_value=False),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0),  # send-keys /exit
            ]

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("output")

                await run_in_tmux(
                    "prompt", "task", project_path, "builder", project_path,
                )

            tmux_call = mock_run.call_args_list[0]
            cmd = tmux_call[0][0]
            wrapper_path = str(tmp_path / "tmp" / "wrapper.sh")
            assert wrapper_path in cmd

    async def test_sentinel_exitcode_race_waits_for_exitcode(self, tmp_path: Path) -> None:
        """Sentinel exists but exitcode file appears after a delay — verify correct exit code is returned."""
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        exitcode_written = False
        original_sleep = asyncio.sleep

        async def mock_wait_for_exitcode(exitcode_file: Path) -> int:
            nonlocal exitcode_written
            for _ in range(30):
                if exitcode_file.exists():
                    return int(exitcode_file.read_text().strip())
                await original_sleep(0.01)
            return 1

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._wait_for_sentinel", new_callable=AsyncMock, return_value=True),
            patch("factory.runners._tmux_persist._wait_for_window_exit", new_callable=AsyncMock),
            patch("factory.runners._tmux_persist._wait_for_exitcode", side_effect=mock_wait_for_exitcode),
            patch("factory.runners._tmux_persist._session_exists", return_value=False),
            patch("factory.runners._tmux_persist._window_exists", return_value=False),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # new-session
                MagicMock(returncode=0),  # send-keys /exit
            ]

            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()
                (tmpdir / "output.log").write_text("output")
                # Delay writing exitcode — simulates race
                async def write_exitcode_later() -> None:
                    await original_sleep(0.05)
                    (tmpdir / "exitcode").write_text("0")

                task = asyncio.create_task(write_exitcode_later())
                stdout, code, _ = await run_in_tmux(
                    "prompt", "task", project_path, "builder", project_path,
                )
                await task

        assert code == 0

    async def test_cancelled_error_cleans_up(self, tmp_path: Path) -> None:
        """CancelledError during _wait_for_sentinel cleans up tmpdir and kills tmux window."""
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        async def sentinel_raises_cancelled(*args, **kwargs) -> bool:
            raise asyncio.CancelledError()

        kill_window_called = False

        def track_subprocess_run(cmd, *args, **kwargs):
            nonlocal kill_window_called
            if isinstance(cmd, list) and "kill-window" in cmd:
                kill_window_called = True
            return MagicMock(returncode=0)

        with (
            patch("factory.runners._tmux_persist.subprocess.run", side_effect=track_subprocess_run),
            patch("factory.runners._tmux_persist._wait_for_sentinel", side_effect=sentinel_raises_cancelled),
            patch("factory.runners._tmux_persist._session_exists", return_value=False),
            patch("factory.runners._tmux_persist._window_exists", return_value=True),
        ):
            with patch("factory.runners._tmux_persist.tempfile.mkdtemp", return_value=str(tmp_path / "tmp")):
                tmpdir = tmp_path / "tmp"
                tmpdir.mkdir()

                try:
                    await run_in_tmux(
                        "prompt", "task", project_path, "builder", project_path,
                    )
                    assert False, "Should have raised CancelledError"
                except asyncio.CancelledError:
                    pass

        assert kill_window_called, "kill-window should have been called on cancellation"
        assert not tmpdir.exists(), "tmpdir should have been cleaned up"


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
