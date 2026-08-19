"""Tests for factory/runners/ — Runner protocol and implementations."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.runners import ClaudeRunner, get_runner
from factory.runners.protocol import RunnerMeta
from factory.models import AgentRunRequest, AgentRunResult


class TestGetRunner:
    def test_default_is_claude(self) -> None:
        runner = get_runner()
        assert runner.name == "claude"

    def test_explicit_claude(self) -> None:
        runner = get_runner("claude")
        assert runner.name == "claude"

    def test_unknown_runner_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown runner 'unknown'"):
            get_runner("unknown")


class TestClaudeRunner:
    async def test_headless_builds_correct_command(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()

        with patch(
            "factory.runners._subprocess.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (
                b'{"result":"output","usage":{},"cost_usd":0,"duration_ms":0,"num_turns":1,"model":"claude-opus-4-7"}',
                b"",
            )

            with patch(
                "factory.runners._subprocess.asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                result = await runner.headless(
                    AgentRunRequest(
                        prompt="You are a test agent.",
                        task="Say hello",
                        cwd=tmp_path,
                        timeout=60.0,
                        model="claude-opus-4-7",
                    )
                )

                assert result.return_code == 0
                assert result.stdout == "output"
                assert result.usage is not None

                call_args = mock_exec.call_args
                all_args = list(call_args[0])
                assert all_args[0] == "claude"
                assert "--append-system-prompt-file" in all_args
                assert "-p" in all_args
                assert "--dangerously-skip-permissions" in all_args
                assert "--model" in all_args
                assert "claude-opus-4-7" in all_args
                assert "--output-format" in all_args
                assert "stream-json" in all_args
                assert "--verbose" in all_args

    async def test_headless_separates_prompt_and_task(self, tmp_path: Path) -> None:
        """headless() writes prompt to a temp file via --append-system-prompt-file and task via -p."""
        runner = ClaudeRunner()

        with patch(
            "factory.runners._subprocess.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b'{"result":"ok"}', b"")

            with patch(
                "factory.runners._subprocess.asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                await runner.headless(
                    AgentRunRequest(
                        prompt="You are the CEO.",
                        task="Run the experiment",
                        cwd=tmp_path,
                    )
                )

                cmd = list(mock_exec.call_args[0])
                assert "--append-system-prompt-file" in cmd
                p_idx = cmd.index("-p")
                assert cmd[p_idx + 1] == "Run the experiment"

    async def test_interactive_run_uses_append_system_prompt_file(self, tmp_path: Path) -> None:
        """interactive_run() uses --append-system-prompt-file (not inline --append-system-prompt)."""
        runner = ClaudeRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            runner.interactive_run(
                AgentRunRequest(
                    prompt="You are the CEO.",
                    task="Start session",
                    cwd=tmp_path,
                )
            )

            cmd = mock_run.call_args[0][0]
            assert "--append-system-prompt-file" in cmd
            assert "--append-system-prompt" not in [
                c for c in cmd if c != "--append-system-prompt-file"
            ]


class TestTelemetryPlatformSuppression:
    def test_headless_sets_telemetry_platform_empty(self, tmp_path: Path) -> None:
        """ClaudeRunner.headless() sets TELEMETRY_PLATFORM='' to suppress native tracing."""
        runner = ClaudeRunner()
        _, env, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )
        )
        env["TELEMETRY_PLATFORM"] = ""
        assert env["TELEMETRY_PLATFORM"] == ""
        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_interactive_sets_telemetry_platform_empty(self, tmp_path: Path) -> None:
        """ClaudeRunner.interactive_run() sets TELEMETRY_PLATFORM='' to suppress native tracing."""
        runner = ClaudeRunner()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            runner.interactive_run(
                AgentRunRequest(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                )
            )

            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["env"]["TELEMETRY_PLATFORM"] == ""

    async def test_headless_subprocess_env_suppresses_telemetry(self, tmp_path: Path) -> None:
        """The actual subprocess env in headless() contains TELEMETRY_PLATFORM=''."""
        runner = ClaudeRunner()

        with patch(
            "factory.runners._subprocess.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b'{"result":"ok"}', b"")

            with patch(
                "factory.runners._subprocess.asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                await runner.headless(
                    AgentRunRequest(
                        prompt="Test",
                        task="Test",
                        cwd=tmp_path,
                    )
                )

                call_kwargs = mock_exec.call_args.kwargs
                assert call_kwargs["env"]["TELEMETRY_PLATFORM"] == ""


class TestStreamingOutput:
    """Tests for streaming subprocess output to terminal."""

    def test_should_stream_defaults_true_with_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FACTORY_RUNNER_QUIET", raising=False)

        from factory.runners._stream import should_stream

        with patch("sys.stdout.isatty", return_value=True):
            assert should_stream() is True

    def test_should_stream_false_when_quiet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER_QUIET", "1")

        from factory.runners._stream import should_stream

        with patch("sys.stdout.isatty", return_value=True):
            assert should_stream() is False

    def test_should_stream_false_when_not_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FACTORY_RUNNER_QUIET", raising=False)

        from factory.runners._stream import should_stream

        with patch("sys.stdout.isatty", return_value=False):
            assert should_stream() is False

    async def test_tee_stream_collects_output(self) -> None:
        from io import BytesIO

        from factory.runners._stream import tee_stream

        class MockReader:
            def __init__(self, lines: list[bytes]) -> None:
                self.lines = iter(lines)

            async def readline(self) -> bytes:
                try:
                    return next(self.lines)
                except StopIteration:
                    return b""

        reader = MockReader([b"line1\n", b"line2\n", b"line3\n"])
        dest = BytesIO()
        buffer: list[bytes] = []

        await tee_stream(reader, dest, buffer, stream=False)  # type: ignore[arg-type]

        assert buffer == [b"line1\n", b"line2\n", b"line3\n"]

    async def test_tee_stream_writes_to_dest_when_streaming(self) -> None:
        from io import BytesIO

        from factory.runners._stream import tee_stream

        class MockReader:
            def __init__(self, lines: list[bytes]) -> None:
                self.lines = iter(lines)

            async def readline(self) -> bytes:
                try:
                    return next(self.lines)
                except StopIteration:
                    return b""

        reader = MockReader([b"hello\n", b"world\n"])
        dest = BytesIO()
        buffer: list[bytes] = []

        await tee_stream(reader, dest, buffer, stream=True)  # type: ignore[arg-type]

        assert dest.getvalue() == b"hello\nworld\n"
        assert buffer == [b"hello\n", b"world\n"]

    async def test_tee_stream_adds_prefix(self) -> None:
        from io import BytesIO

        from factory.runners._stream import tee_stream

        class MockReader:
            def __init__(self, lines: list[bytes]) -> None:
                self.lines = iter(lines)

            async def readline(self) -> bytes:
                try:
                    return next(self.lines)
                except StopIteration:
                    return b""

        reader = MockReader([b"line1\n", b"line2\n"])
        dest = BytesIO()
        buffer: list[bytes] = []

        await tee_stream(
            reader,  # type: ignore[arg-type]
            dest,
            buffer,
            stream=True,
            prefix=b"[test] ",
        )

        assert dest.getvalue() == b"[test] line1\n[test] line2\n"
        assert buffer == [b"line1\n", b"line2\n"]

    async def test_stream_subprocess_collects_both_streams(self) -> None:
        from factory.runners._stream import stream_subprocess

        class MockReader:
            def __init__(self, lines: list[bytes]) -> None:
                self.lines = iter(lines)

            async def readline(self) -> bytes:
                try:
                    return next(self.lines)
                except StopIteration:
                    return b""

        class MockProc:
            def __init__(self) -> None:
                self.stdout = MockReader([b"stdout line\n"])
                self.stderr = MockReader([b"stderr line\n"])

            async def wait(self) -> int:
                return 0

        proc = MockProc()

        stdout, stderr = await stream_subprocess(proc, stream=False)  # type: ignore[arg-type]

        assert stdout == b"stdout line\n"
        assert stderr == b"stderr line\n"

    async def test_claude_runner_uses_streaming(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_RUNNER_QUIET", raising=False)

        runner = ClaudeRunner()

        with patch("factory.runners._subprocess.should_stream", return_value=True):
            with patch(
                "factory.runners._subprocess.stream_subprocess", new_callable=AsyncMock
            ) as mock_stream:
                mock_stream.return_value = (b'{"result":"output"}', b"")

                with patch(
                    "factory.runners._subprocess.asyncio.create_subprocess_exec",
                    new_callable=AsyncMock,
                ) as mock_exec:
                    mock_proc = AsyncMock()
                    mock_proc.returncode = 0
                    mock_exec.return_value = mock_proc

                    await runner.headless(
                        AgentRunRequest(
                            prompt="Test",
                            task="Test",
                            cwd=tmp_path,
                            role="researcher",
                        )
                    )

                    mock_stream.assert_called_once()
                    call_kwargs = mock_stream.call_args.kwargs
                    assert call_kwargs["stream"] is True
                    assert call_kwargs["prefix"] == "[claude:researcher]"

    async def test_quiet_mode_disables_streaming(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FACTORY_RUNNER_QUIET", "1")

        runner = ClaudeRunner()

        with patch(
            "factory.runners._subprocess.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b'{"result":"output"}', b"")

            with patch(
                "factory.runners._subprocess.asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                await runner.headless(
                    AgentRunRequest(
                        prompt="Test",
                        task="Test",
                        cwd=tmp_path,
                        role="researcher",
                    )
                )

                mock_stream.assert_called_once()
                call_kwargs = mock_stream.call_args.kwargs
                assert call_kwargs["stream"] is False

    async def test_output_saved_to_review_file_matches_buffer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_RUNNER_QUIET", raising=False)

        (tmp_path / ".factory" / "reviews").mkdir(parents=True)

        from factory.agents.runner import invoke_agent

        json_output = json.dumps(
            {"result": "Line 1\nLine 2\nLine 3\n", "usage": {}, "cost_usd": 0.01}
        )

        with patch(
            "factory.runners._subprocess.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (json_output.encode(), b"")

            with patch(
                "factory.runners._subprocess.asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                stdout, code = await invoke_agent(
                    "researcher",
                    "Test task",
                    tmp_path,
                    runner_name="claude",
                )

                assert "Line 1" in stdout

                review_file = tmp_path / ".factory" / "reviews" / "researcher-latest.md"
                assert review_file.exists()
                content = review_file.read_text()
                assert "Line 1" in content
                assert "Line 2" in content
                assert "Line 3" in content


class TestAnsiSanitization:
    """Tests for strip_ansi + sanitize on the live-terminal write path."""

    def test_strip_ansi_removes_csi_color_and_cursor(self) -> None:
        from factory.runners._stream import strip_ansi

        assert strip_ansi(b"\x1b[1;36mhi\x1b[0m") == b"hi"
        assert strip_ansi(b"\x1b[38:2:255:0:0mred\x1b[0m") == b"red"
        assert strip_ansi(b"\x1b[2J\x1b[H") == b""

    def test_strip_ansi_removes_alt_screen_and_cursor_toggle(self) -> None:
        from factory.runners._stream import strip_ansi

        assert strip_ansi(b"\x1b[?1049h") == b""
        assert strip_ansi(b"\x1b[?1049l") == b""
        assert strip_ansi(b"\x1b[?25l") == b""
        assert strip_ansi(b"\x1b[?25h") == b""

    def test_strip_ansi_removes_osc_window_title(self) -> None:
        from factory.runners._stream import strip_ansi

        assert strip_ansi(b"\x1b]0;title\x07rest") == b"rest"
        assert strip_ansi(b"\x1b]0;title\x1b\\rest") == b"rest"

    def test_strip_ansi_removes_string_sequences(self) -> None:
        from factory.runners._stream import strip_ansi

        assert strip_ansi(b"\x1bP1$r0m\x1b\\after") == b"after"
        assert strip_ansi(b"\x1b_payload\x1b\\after") == b"after"
        assert strip_ansi(b"\x1b^foo\x1b\\after") == b"after"
        assert strip_ansi(b"\x1bXsos\x1b\\after") == b"after"

    def test_strip_ansi_removes_decsc_decrc_ri(self) -> None:
        from factory.runners._stream import strip_ansi

        assert strip_ansi(b"\x1b7save\x1b8") == b"save"
        assert strip_ansi(b"\x1bMup") == b"up"

    def test_strip_ansi_preserves_plaintext_and_newlines(self) -> None:
        from factory.runners._stream import strip_ansi

        assert strip_ansi(b"plain text\n") == b"plain text\n"
        assert strip_ansi(b"a\rb\n") == b"ab\n"
        assert strip_ansi(b"a\r\nb\r\n") == b"a\nb\n"
        utf8 = "café — 日本語".encode()
        assert strip_ansi(utf8) == utf8

    async def test_tee_stream_sanitize_strips_dest_keeps_buffer_raw(self) -> None:
        from io import BytesIO

        from factory.runners._stream import tee_stream

        class MockReader:
            def __init__(self, lines: list[bytes]) -> None:
                self.lines = iter(lines)

            async def readline(self) -> bytes:
                try:
                    return next(self.lines)
                except StopIteration:
                    return b""

        reader = MockReader([b"\x1b[2J\x1b[Hhello\n"])
        dest = BytesIO()
        buffer: list[bytes] = []

        await tee_stream(reader, dest, buffer, stream=True, sanitize=True)  # type: ignore[arg-type]

        assert dest.getvalue() == b"hello\n"
        assert buffer == [b"\x1b[2J\x1b[Hhello\n"]

    async def test_tee_stream_sanitize_skips_redraw_only_lines(self) -> None:
        from io import BytesIO

        from factory.runners._stream import tee_stream

        class MockReader:
            def __init__(self, lines: list[bytes]) -> None:
                self.lines = iter(lines)

            async def readline(self) -> bytes:
                try:
                    return next(self.lines)
                except StopIteration:
                    return b""

        reader = MockReader([b"\x1b[32mok\n", b"\x1b[2J\x1b[H\n"])
        dest = BytesIO()
        buffer: list[bytes] = []

        await tee_stream(
            reader,  # type: ignore[arg-type]
            dest,
            buffer,
            stream=True,
            prefix=b"[test] ",
            sanitize=True,
        )

        assert dest.getvalue() == b"[test] ok\n"
        assert buffer == [b"\x1b[32mok\n", b"\x1b[2J\x1b[H\n"]

    async def test_tee_stream_sanitize_preserves_genuine_blank_line(self) -> None:
        from io import BytesIO

        from factory.runners._stream import tee_stream

        class MockReader:
            def __init__(self, lines: list[bytes]) -> None:
                self.lines = iter(lines)

            async def readline(self) -> bytes:
                try:
                    return next(self.lines)
                except StopIteration:
                    return b""

        reader = MockReader([b"hello\n", b"\n", b"world\n"])
        dest = BytesIO()
        buffer: list[bytes] = []

        await tee_stream(reader, dest, buffer, stream=True, sanitize=True)  # type: ignore[arg-type]

        assert dest.getvalue() == b"hello\n\nworld\n"
        assert buffer == [b"hello\n", b"\n", b"world\n"]

    async def test_tee_stream_sanitize_false_byte_identical(self) -> None:
        from io import BytesIO

        from factory.runners._stream import tee_stream

        class MockReader:
            def __init__(self, lines: list[bytes]) -> None:
                self.lines = iter(lines)

            async def readline(self) -> bytes:
                try:
                    return next(self.lines)
                except StopIteration:
                    return b""

        raw = b"\x1b[2J\x1b[Hhello\n"
        reader = MockReader([raw])
        dest = BytesIO()
        buffer: list[bytes] = []

        await tee_stream(reader, dest, buffer, stream=True)  # type: ignore[arg-type]

        assert dest.getvalue() == raw
        assert buffer == [raw]

    async def test_stream_subprocess_threads_sanitize_to_both(self) -> None:
        from factory.runners._stream import stream_subprocess

        class MockReader:
            def __init__(self, lines: list[bytes]) -> None:
                self.lines = iter(lines)

            async def readline(self) -> bytes:
                try:
                    return next(self.lines)
                except StopIteration:
                    return b""

        class MockProc:
            def __init__(self) -> None:
                self.stdout = MockReader([b"out\n"])
                self.stderr = MockReader([b"err\n"])

            async def wait(self) -> int:
                return 0

        proc = MockProc()

        with patch("factory.runners._stream.tee_stream", new_callable=AsyncMock) as mock_tee:
            await stream_subprocess(proc, stream=False, sanitize=True)  # type: ignore[arg-type]

            assert mock_tee.call_count == 2
            for call in mock_tee.call_args_list:
                assert call.kwargs["sanitize"] is True

    async def test_claude_runner_sanitizes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_RUNNER_QUIET", raising=False)

        runner = ClaudeRunner()

        with patch("factory.runners.claude.run_subprocess", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AgentRunResult(
                stdout='{"result":"output"}',
                return_code=0,
            )

            await runner.headless(
                AgentRunRequest(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    role="researcher",
                )
            )

            mock_run.assert_called_once()
            assert mock_run.call_args.kwargs.get("sanitize", False) is True


class TestInactivityTimeout:
    """Tests for the inactivity-based timeout watchdog."""

    async def test_inactivity_timeout_kills_silent_process(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            "python3",
            "-c",
            "import time; print('hello', flush=True); time.sleep(60)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        from factory.runners._stream import stream_subprocess

        stdout, stderr = await stream_subprocess(
            proc,
            stream=False,
            inactivity_timeout=0.5,
        )

        assert proc.returncode == -9
        assert b"hello" in stdout

    async def test_active_output_prevents_timeout(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            "python3",
            "-c",
            "import time\nfor i in range(6):\n    print(f'tick {i}', flush=True)\n    time.sleep(0.2)\n",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        from factory.runners._stream import stream_subprocess

        stdout, stderr = await stream_subprocess(
            proc,
            stream=False,
            inactivity_timeout=0.8,
        )

        assert proc.returncode == 0
        assert b"tick 5" in stdout

    async def test_max_timeout_backstop(self) -> None:
        from factory.runners._subprocess import run_subprocess

        result = await run_subprocess(
            [
                "python3",
                "-c",
                "import time\nwhile True:\n    print('.', flush=True)\n    time.sleep(0.1)\n",
            ],
            cwd=".",
            env=dict(os.environ),
            timeout=999.0,
            runner_name="test",
            role="test",
            max_timeout=1.0,
        )

        assert result.return_code == 1
        assert "max wall-clock timeout" in result.stdout.lower()


class TestRunnerMetaCustomAuthCheck:
    """Tests for RunnerMeta.custom_auth_check support."""

    def test_custom_auth_check_used_when_provided(self) -> None:
        meta = RunnerMeta(
            name="test",
            display_name="Test",
            binary="test",
            install_hint="test",
            custom_auth_check=lambda: True,
        )
        assert meta.check_auth() is True

    def test_falls_back_to_env_var_check_without_custom(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SOME_KEY", raising=False)
        meta = RunnerMeta(
            name="test",
            display_name="Test",
            binary="test",
            install_hint="test",
            required_env_vars=["SOME_KEY"],
        )
        assert meta.check_auth() is False


class TestSaveReview:
    """Tests for _save_review with and without review_tag."""

    def test_save_review_with_tag(self, tmp_path: Path) -> None:
        from factory.agents.runner import _save_review

        project = tmp_path / "proj"
        project.mkdir()
        _save_review(project, "researcher", "some output", 0, review_tag="codebase")
        reviews = project / ".factory" / "reviews"
        assert (reviews / "researcher-codebase-latest.md").exists()
        content = (reviews / "researcher-codebase-latest.md").read_text()
        assert "some output" in content
        assert "exit_code:** 0" in content
        assert not (reviews / "researcher-latest.md").exists()

    def test_save_review_without_tag(self, tmp_path: Path) -> None:
        from factory.agents.runner import _save_review

        project = tmp_path / "proj"
        project.mkdir()
        _save_review(project, "researcher", "output text", 0)
        reviews = project / ".factory" / "reviews"
        assert (reviews / "researcher-latest.md").exists()
        content = (reviews / "researcher-latest.md").read_text()
        assert "output text" in content


class TestClaudeBuildInteractiveCommand:
    """Tests for ClaudeRunner.build_interactive_command()."""

    def test_base_command_structure(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        cmd, env, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="You are the CEO.",
                task="Start session",
                cwd=tmp_path,
            )
        )

        assert cmd[0] == "claude"
        assert "--append-system-prompt-file" in cmd
        assert "Start session" in cmd
        assert "-p" not in cmd
        assert "--output-format" not in cmd

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_permission_flag(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        cmd, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                skip_permissions=True,
            )
        )

        assert "--dangerously-skip-permissions" in cmd

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_no_permission_flag_when_not_skipped(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        cmd, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                skip_permissions=False,
            )
        )

        assert "--dangerously-skip-permissions" not in cmd

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_model_flag_and_env(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        cmd, env, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                model="claude-opus-4-7",
            )
        )

        assert "--model" in cmd
        assert "claude-opus-4-7" in cmd
        assert env["FACTORY_MODEL"] == "claude-opus-4-7"

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_session_name_flag(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        cmd, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                session_name="my-session",
            )
        )

        assert "--name" in cmd
        assert "my-session" in cmd

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_env_strips_virtual_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        runner = ClaudeRunner()
        _, env, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )
        )

        assert "VIRTUAL_ENV" not in env

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_temp_files_include_prompt_and_claude_md_and_settings(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        _, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test prompt content",
                task="Test",
                cwd=tmp_path,
            )
        )

        assert len(temp_files) == 3
        prompt_file = temp_files[0]
        claude_md = temp_files[1]
        settings_file = temp_files[2]

        assert prompt_file.exists()
        assert prompt_file.read_text() == "Test prompt content"
        assert claude_md == tmp_path / ".claude" / "CLAUDE.md"
        assert settings_file == tmp_path / ".claude" / "settings.local.json"

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_writes_claude_md_with_prompt(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        prompt = "You are the CEO.\n\n## Instructions\nDo great things."
        _, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt=prompt,
                task="Test",
                cwd=tmp_path,
            )
        )

        claude_md = tmp_path / ".claude" / "CLAUDE.md"
        assert claude_md.exists()
        assert claude_md.read_text() == prompt

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_creates_claude_dir_if_missing(self, tmp_path: Path) -> None:
        assert not (tmp_path / ".claude").exists()

        runner = ClaudeRunner()
        _, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )
        )

        assert (tmp_path / ".claude").is_dir()

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_writes_settings_local_json(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        _, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )
        )

        settings_path = tmp_path / ".claude" / "settings.local.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        assert settings["disallowedTools"] == ["Agent"]

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_merges_existing_settings_local_json(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings_path = claude_dir / "settings.local.json"
        settings_path.write_text(
            json.dumps({"existingKey": "value", "disallowedTools": ["OldTool"]})
        )

        runner = ClaudeRunner()
        _, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )
        )

        settings = json.loads(settings_path.read_text())
        assert settings["existingKey"] == "value"
        assert settings["disallowedTools"] == ["Agent"]

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_handles_corrupt_settings_local_json(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.local.json").write_text("not valid json{{{")

        runner = ClaudeRunner()
        _, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )
        )

        settings = json.loads((claude_dir / "settings.local.json").read_text())
        assert settings["disallowedTools"] == ["Agent"]

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_no_disallowed_tools_in_cmd(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        cmd, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )
        )

        assert "--disallowedTools" not in cmd

        for f in temp_files:
            f.unlink(missing_ok=True)


class TestDisallowedAgentTool:
    """Tests for --disallowedTools Agent across all Claude Code execution paths."""

    def test_build_command_includes_disallowed_tools(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        cmd, _, temp_files = runner.build_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )
        )

        assert "--disallowedTools" in cmd
        dt_idx = cmd.index("--disallowedTools")
        assert cmd[dt_idx + 1] == "Agent"

        for f in temp_files:
            f.unlink(missing_ok=True)

    def test_build_interactive_command_uses_settings_not_cli_flag(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()
        cmd, _, temp_files = runner.build_interactive_command(
            AgentRunRequest(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
            )
        )

        assert "--disallowedTools" not in cmd

        settings_path = tmp_path / ".claude" / "settings.local.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        assert settings["disallowedTools"] == ["Agent"]

        for f in temp_files:
            f.unlink(missing_ok=True)

    async def test_headless_subprocess_receives_disallowed_tools(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()

        with patch(
            "factory.runners._subprocess.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b'{"result":"ok"}', b"")

            with patch(
                "factory.runners._subprocess.asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                await runner.headless(
                    AgentRunRequest(
                        prompt="Test",
                        task="Test",
                        cwd=tmp_path,
                    )
                )

                all_args = list(mock_exec.call_args[0])
                assert "--disallowedTools" in all_args
                dt_idx = all_args.index("--disallowedTools")
                assert all_args[dt_idx + 1] == "Agent"

    async def test_background_command_includes_disallowed_tools(self, tmp_path: Path) -> None:
        from factory.runners._background import run_in_background

        with (
            patch("factory.runners._background.subprocess.run") as mock_run,
            patch("factory.runners._background.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_run.return_value = type(
                "R", (), {"stdout": "backgrounded · abc123", "stderr": "", "returncode": 0}
            )()

            await run_in_background(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                role="test",
                timeout=0.1,
            )

            cmd = mock_run.call_args_list[0][0][0]
            assert "--disallowedTools" in cmd
            dt_idx = cmd.index("--disallowedTools")
            assert cmd[dt_idx + 1] == "Agent"

    async def test_tmux_command_includes_disallowed_tools(self, tmp_path: Path) -> None:
        from factory.runners._tmux_persist import run_in_tmux

        with (
            patch("factory.runners._tmux_persist.subprocess.run") as mock_run,
            patch("factory.runners._tmux_persist._session_exists", return_value=True),
            patch("factory.runners._tmux_persist._window_exists", return_value=False),
            patch("factory.runners._tmux_persist._generate_settings") as mock_settings,
            patch("factory.runners._tmux_persist._cleanup"),
        ):
            mock_settings.return_value = tmp_path / "settings.json"
            (tmp_path / "settings.json").write_text("{}")
            mock_run.return_value = type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()

            await run_in_tmux(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                role="test",
                project_path=tmp_path,
                timeout=0.1,
            )

            first_call_args = mock_run.call_args_list[0][0][0]
            wrapper_script_path = first_call_args[-1]
            wrapper_content = Path(wrapper_script_path).read_text()
            assert "--disallowedTools" in wrapper_content
            assert "Agent" in wrapper_content


class TestGetRunnerChoices:
    """Tests for get_runner_choices() — returns sorted list of runner names."""

    def test_returns_sorted_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.runners import get_runner_choices

        import factory.runners as runners_mod

        monkeypatch.setattr(runners_mod, "_entrypoints_loaded", True)

        choices = get_runner_choices()
        assert isinstance(choices, list)
        assert choices == sorted(choices)
        assert "claude" in choices

    def test_returns_strings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.runners import get_runner_choices

        import factory.runners as runners_mod

        monkeypatch.setattr(runners_mod, "_entrypoints_loaded", True)

        choices = get_runner_choices()
        assert all(isinstance(c, str) for c in choices)


class TestGetAllRunnerMeta:
    """Tests for get_all_runner_meta() — returns metadata for all runners."""

    def test_returns_list_of_runner_meta(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.runners import get_all_runner_meta

        import factory.runners as runners_mod

        monkeypatch.setattr(runners_mod, "_entrypoints_loaded", True)

        metas = get_all_runner_meta()
        assert isinstance(metas, list)
        assert len(metas) > 0
        assert all(isinstance(m, RunnerMeta) for m in metas)

    def test_includes_claude_runner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.runners import get_all_runner_meta

        import factory.runners as runners_mod

        monkeypatch.setattr(runners_mod, "_entrypoints_loaded", True)

        metas = get_all_runner_meta()
        names = {m.name for m in metas}
        assert "claude" in names

    def test_handles_runner_without_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.runners import get_all_runner_meta

        import factory.runners as runners_mod

        monkeypatch.setattr(runners_mod, "_entrypoints_loaded", True)

        class FakeRunner:
            name = "fake"

        original_runners = dict(runners_mod._RUNNERS)
        try:
            runners_mod._RUNNERS["fake"] = FakeRunner  # type: ignore[assignment]
            metas = get_all_runner_meta()
            fake_names = [m.name for m in metas if m.name == "fake"]
            assert len(fake_names) == 0
        finally:
            runners_mod._RUNNERS.clear()
            runners_mod._RUNNERS.update(original_runners)


class TestGetAvailableRunners:
    """Tests for get_available_runners() — returns all registered runners."""

    def test_returns_dict_copy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.runners import get_available_runners

        import factory.runners as runners_mod

        monkeypatch.setattr(runners_mod, "_entrypoints_loaded", True)

        runners = get_available_runners()
        assert isinstance(runners, dict)
        runners["new_key"] = "test"  # type: ignore[assignment]
        runners2 = get_available_runners()
        assert "new_key" not in runners2

    def test_includes_claude_runner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.runners import get_available_runners

        import factory.runners as runners_mod

        monkeypatch.setattr(runners_mod, "_entrypoints_loaded", True)

        runners = get_available_runners()
        assert "claude" in runners


class TestLoadEntrypointRunners:
    """Tests for _load_entrypoint_runners() — entry_points discovery."""

    def test_loads_only_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import factory.runners as runners_mod

        monkeypatch.setattr(runners_mod, "_entrypoints_loaded", False)

        with patch("factory.runners.entry_points", create=True):
            runners_mod._load_entrypoint_runners()
            runners_mod._load_entrypoint_runners()

        assert runners_mod._entrypoints_loaded is True

    def test_loads_plugin_runner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import factory.runners as runners_mod

        monkeypatch.setattr(runners_mod, "_entrypoints_loaded", False)
        original_runners = dict(runners_mod._RUNNERS)

        class PluginRunner:
            name = "plugin"

        mock_ep = MagicMock()
        mock_ep.name = "plugin"
        mock_ep.load.return_value = PluginRunner

        try:
            with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
                runners_mod._load_entrypoint_runners()

            assert "plugin" in runners_mod._RUNNERS
            assert runners_mod._RUNNERS["plugin"] is PluginRunner
        finally:
            runners_mod._RUNNERS.clear()
            runners_mod._RUNNERS.update(original_runners)
            monkeypatch.setattr(runners_mod, "_entrypoints_loaded", False)

    def test_skips_existing_runner_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import factory.runners as runners_mod

        monkeypatch.setattr(runners_mod, "_entrypoints_loaded", False)
        original_claude = runners_mod._RUNNERS["claude"]

        mock_ep = MagicMock()
        mock_ep.name = "claude"
        mock_ep.load.return_value = MagicMock()

        try:
            with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
                runners_mod._load_entrypoint_runners()

            assert runners_mod._RUNNERS["claude"] is original_claude
            mock_ep.load.assert_not_called()
        finally:
            monkeypatch.setattr(runners_mod, "_entrypoints_loaded", False)

    def test_handles_plugin_load_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import factory.runners as runners_mod

        monkeypatch.setattr(runners_mod, "_entrypoints_loaded", False)
        original_runners = dict(runners_mod._RUNNERS)

        mock_ep = MagicMock()
        mock_ep.name = "broken_plugin"
        mock_ep.load.side_effect = RuntimeError("plugin load failed")

        try:
            with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
                runners_mod._load_entrypoint_runners()

            assert "broken_plugin" not in runners_mod._RUNNERS
        finally:
            runners_mod._RUNNERS.clear()
            runners_mod._RUNNERS.update(original_runners)
            monkeypatch.setattr(runners_mod, "_entrypoints_loaded", False)

    def test_handles_entry_points_import_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import factory.runners as runners_mod

        monkeypatch.setattr(runners_mod, "_entrypoints_loaded", False)

        with patch("importlib.metadata.entry_points", side_effect=Exception("no entry_points")):
            runners_mod._load_entrypoint_runners()

        assert runners_mod._entrypoints_loaded is True
        monkeypatch.setattr(runners_mod, "_entrypoints_loaded", False)
