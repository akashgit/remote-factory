"""Tests for factory/runners/ — Runner protocol and implementations."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.runners import ClaudeRunner, BobRunner, get_runner, is_dry_run
from factory.runners.usage import (
    CeilingExceededError,
    CeilingWarning,
    check_ceilings,
    count_cycle_invocations,
    get_usage_log_path,
    log_usage,
)


class TestGetRunner:
    def test_default_is_claude(self) -> None:
        runner = get_runner()
        assert runner.name == "claude"

    def test_explicit_claude(self) -> None:
        runner = get_runner("claude")
        assert runner.name == "claude"

    def test_explicit_bob(self) -> None:
        runner = get_runner("bob")
        assert runner.name == "bob"

    def test_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER", "bob")
        runner = get_runner()
        assert runner.name == "bob"

    def test_explicit_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER", "bob")
        runner = get_runner("claude")
        assert runner.name == "claude"

    def test_unknown_runner_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown runner 'unknown'"):
            get_runner("unknown")


class TestClaudeRunner:
    async def test_headless_builds_correct_command(self, tmp_path: Path) -> None:
        runner = ClaudeRunner()

        with patch(
            "factory.runners.claude.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b"output", b"")

            with patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                stdout, code = await runner.headless(
                    prompt="You are a test agent.",
                    task="Say hello",
                    cwd=tmp_path,
                    timeout=60.0,
                    model="claude-opus-4-7",
                )

                assert code == 0
                assert stdout == "output"

                call_args = mock_exec.call_args
                cmd = call_args[0]
                assert cmd[0] == "claude"
                assert "-p" in cmd
                assert "--dangerously-skip-permissions" in cmd
                assert "--model" in cmd
                assert "claude-opus-4-7" in cmd


class TestBobRunner:
    def test_is_dry_run_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_BOB_DRY_RUN", "1")
        assert is_dry_run() is True

    def test_is_dry_run_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FACTORY_BOB_DRY_RUN", raising=False)
        assert is_dry_run() is False

    def test_interactive_exec_dry_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """interactive_exec prints dry-run message and exits."""
        monkeypatch.setenv("FACTORY_BOB_DRY_RUN", "1")
        (tmp_path / ".factory").mkdir()

        runner = BobRunner()

        with pytest.raises(SystemExit) as exc_info:
            runner.interactive_exec(
                prompt="Test prompt",
                task="Test task",
                cwd=tmp_path,
                role="ceo",
            )

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "[DRY-RUN]" in captured.out

    async def test_headless_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BobRunner.headless() handles timeout gracefully."""
        import asyncio
        from unittest.mock import AsyncMock, patch

        monkeypatch.setenv("BOBSHELL_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_BOB_DRY_RUN", raising=False)

        import factory.runners.bob as bob_module
        bob_module._auth_checked = False

        (tmp_path / ".factory").mkdir()

        with patch("factory.runners.bob.asyncio.wait_for", side_effect=asyncio.TimeoutError):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.kill = AsyncMock()
                mock_proc.wait = AsyncMock()
                mock_exec.return_value = mock_proc

                runner = BobRunner()
                stdout, code = await runner.headless(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    role="researcher",
                    timeout=0.1,
                )

        assert code == 1
        assert "timed out" in stdout.lower()
        bob_module._auth_checked = False

    def test_count_cycle_invocations_with_datetime(self, tmp_path: Path) -> None:
        """count_cycle_invocations filters by cycle_start datetime."""
        from datetime import datetime, timezone, timedelta
        from factory.runners.usage import count_cycle_invocations, get_usage_log_path
        import json

        (tmp_path / ".factory").mkdir()

        now = datetime.now(timezone.utc)
        old_time = now - timedelta(hours=2)

        log_path = get_usage_log_path(tmp_path)
        entries = [
            {"timestamp": old_time.isoformat(), "role": "a", "cwd": str(tmp_path),
             "duration_seconds": 1.0, "exit_code": 0, "dry_run": False},
            {"timestamp": now.isoformat(), "role": "b", "cwd": str(tmp_path),
             "duration_seconds": 1.0, "exit_code": 0, "dry_run": False},
            {"timestamp": now.isoformat(), "role": "c", "cwd": str(tmp_path),
             "duration_seconds": 1.0, "exit_code": 0, "dry_run": True},
        ]

        with open(log_path, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        cycle_start = now - timedelta(hours=1)
        count = count_cycle_invocations(tmp_path, cycle_start)
        assert count == 1

    async def test_headless_ceiling_exceeded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BobRunner returns error when ceiling exceeded."""
        from datetime import datetime, timezone, timedelta

        monkeypatch.setenv("BOBSHELL_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_BOB_DRY_RUN", raising=False)
        monkeypatch.setenv("FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE", "1")

        import factory.runners.bob as bob_module
        bob_module._auth_checked = False

        (tmp_path / ".factory").mkdir()

        # Create runner FIRST with a cycle_start in the past
        cycle_start = datetime.now(timezone.utc) - timedelta(seconds=5)
        runner = BobRunner(cycle_start=cycle_start)

        # Log entry AFTER cycle_start so it counts
        log_usage(tmp_path, "a", tmp_path, 1.0, 0, dry_run=False)

        stdout, code = await runner.headless(
            prompt="Test",
            task="Test",
            cwd=tmp_path,
            role="researcher",
        )

        assert code == 1
        assert "ceiling" in stdout.lower() or "exceeded" in stdout.lower()
        bob_module._auth_checked = False

    async def test_dry_run_returns_stub(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_BOB_DRY_RUN", "1")

        # Create .factory directory for usage log
        (tmp_path / ".factory").mkdir()

        runner = BobRunner()
        stdout, code = await runner.headless(
            prompt="You are a test agent.",
            task="Say hello",
            cwd=tmp_path,
            role="researcher",
        )

        assert code == 0
        assert "[DRY-RUN]" in stdout
        assert "researcher" in stdout

    async def test_dry_run_logs_usage(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_BOB_DRY_RUN", "1")

        # Create .factory directory
        (tmp_path / ".factory").mkdir()

        runner = BobRunner()
        await runner.headless(
            prompt="Test prompt",
            task="Test task",
            cwd=tmp_path,
            role="builder",
        )

        log_path = get_usage_log_path(tmp_path)
        assert log_path.exists()

        with open(log_path) as f:
            entry = json.loads(f.readline())

        assert entry["role"] == "builder"
        assert entry["dry_run"] is True
        assert entry["exit_code"] == 0


class TestUsageTracking:
    def test_log_usage_creates_file(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()

        log_usage(tmp_path, "researcher", tmp_path, 1.5, 0, dry_run=False)

        log_path = get_usage_log_path(tmp_path)
        assert log_path.exists()

        with open(log_path) as f:
            entry = json.loads(f.readline())

        assert entry["role"] == "researcher"
        assert entry["duration_seconds"] == 1.5
        assert entry["exit_code"] == 0
        assert entry["dry_run"] is False

    def test_count_cycle_invocations_with_start(self, tmp_path: Path) -> None:
        from datetime import datetime, timezone, timedelta

        (tmp_path / ".factory").mkdir()

        # Log some entries
        log_usage(tmp_path, "a", tmp_path, 1.0, 0, dry_run=False)
        log_usage(tmp_path, "b", tmp_path, 1.0, 0, dry_run=False)
        log_usage(tmp_path, "c", tmp_path, 1.0, 0, dry_run=True)  # dry-run, shouldn't count

        # Count from beginning of the current second
        cycle_start = datetime.now(timezone.utc) - timedelta(seconds=5)
        count = count_cycle_invocations(tmp_path, cycle_start)
        assert count == 2  # dry-run excluded

    def test_count_cycle_invocations_none_returns_zero(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir()

        log_usage(tmp_path, "a", tmp_path, 1.0, 0, dry_run=False)
        log_usage(tmp_path, "b", tmp_path, 1.0, 0, dry_run=False)

        # Without cycle_start, returns 0
        count = count_cycle_invocations(tmp_path, None)
        assert count == 0


class TestCeilings:
    def test_check_ceilings_passes_when_under(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import datetime, timezone, timedelta

        (tmp_path / ".factory").mkdir()
        monkeypatch.setenv("FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE", "5")

        # Log a few entries (under ceiling)
        log_usage(tmp_path, "a", tmp_path, 1.0, 0, dry_run=False)
        log_usage(tmp_path, "b", tmp_path, 1.0, 0, dry_run=False)

        # Should not raise
        cycle_start = datetime.now(timezone.utc) - timedelta(seconds=5)
        check_ceilings(tmp_path, cycle_start)

    def test_check_ceilings_fails_on_cycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import datetime, timezone, timedelta

        (tmp_path / ".factory").mkdir()
        monkeypatch.setenv("FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE", "1")

        log_usage(tmp_path, "a", tmp_path, 1.0, 0, dry_run=False)

        cycle_start = datetime.now(timezone.utc) - timedelta(seconds=5)
        with pytest.raises(CeilingExceededError) as exc_info:
            check_ceilings(tmp_path, cycle_start)

        assert exc_info.value.ceiling_name == "per-cycle"
        assert exc_info.value.env_var == "FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE"

    def test_ceiling_error_message_is_actionable(self) -> None:
        error = CeilingExceededError("per-cycle", 5, 5, "FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE")
        msg = str(error)

        assert "ceiling exceeded" in msg.lower()
        assert "5/5" in msg
        assert "FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE=10" in msg  # suggests bumping


class TestCeilingWarning:
    def test_warning_returned_when_cycle_ceiling_near(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """check_ceilings returns CeilingWarning when ≤2 cycle invocations remain."""
        from datetime import datetime, timezone, timedelta

        (tmp_path / ".factory").mkdir()
        monkeypatch.setenv("FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE", "4")

        log_usage(tmp_path, "a", tmp_path, 1.0, 0, dry_run=False)
        log_usage(tmp_path, "b", tmp_path, 1.0, 0, dry_run=False)

        cycle_start = datetime.now(timezone.utc) - timedelta(seconds=5)
        warning = check_ceilings(tmp_path, cycle_start)

        assert warning is not None
        assert isinstance(warning, CeilingWarning)
        assert warning.ceiling_name == "per-cycle"
        assert warning.remaining == 2
        assert warning.limit == 4

    def test_no_warning_when_sufficient_invocations_remain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """check_ceilings returns None when >2 invocations remain."""
        from datetime import datetime, timezone, timedelta

        (tmp_path / ".factory").mkdir()
        monkeypatch.setenv("FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE", "10")

        log_usage(tmp_path, "a", tmp_path, 1.0, 0, dry_run=False)

        cycle_start = datetime.now(timezone.utc) - timedelta(seconds=5)
        warning = check_ceilings(tmp_path, cycle_start)

        assert warning is None

    def test_warning_at_exactly_one_remaining(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """check_ceilings returns CeilingWarning when exactly 1 invocation remains."""
        from datetime import datetime, timezone, timedelta

        (tmp_path / ".factory").mkdir()
        monkeypatch.setenv("FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE", "3")

        log_usage(tmp_path, "a", tmp_path, 1.0, 0, dry_run=False)
        log_usage(tmp_path, "b", tmp_path, 1.0, 0, dry_run=False)

        cycle_start = datetime.now(timezone.utc) - timedelta(seconds=5)
        warning = check_ceilings(tmp_path, cycle_start)

        assert warning is not None
        assert warning.ceiling_name == "per-cycle"
        assert warning.remaining == 1


class TestBobAuthPreflight:
    async def test_auth_check_fails_without_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_BOB_DRY_RUN", raising=False)
        monkeypatch.delenv("BOBSHELL_API_KEY", raising=False)

        # Reset the auth check state
        import factory.runners.bob as bob_module
        bob_module._auth_checked = False

        (tmp_path / ".factory").mkdir()

        runner = BobRunner()

        from factory.runners.bob import BobAuthError

        with pytest.raises(BobAuthError):
            await runner.headless(
                prompt="Test",
                task="Test",
                cwd=tmp_path,
                role="researcher",
            )

    async def test_auth_check_passes_with_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACTORY_BOB_DRY_RUN", raising=False)
        monkeypatch.setenv("BOBSHELL_API_KEY", "test-key")

        # Reset the auth check state
        import factory.runners.bob as bob_module
        bob_module._auth_checked = False

        (tmp_path / ".factory").mkdir()

        # Mock the streaming subprocess to avoid actual bob invocation
        with patch(
            "factory.runners.bob.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b"output", b"")

            with patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                runner = BobRunner()
                stdout, code = await runner.headless(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    role="researcher",
                )

                assert code == 0


class TestKeyPersistence:
    """Tests for file-based API key persistence."""

    def test_persist_key_creates_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify _persist_key writes the key to .factory/.bob_auth."""
        monkeypatch.setenv("BOBSHELL_API_KEY", "test-secret-key")

        (tmp_path / ".factory").mkdir()

        from factory.runners.bob import _persist_key

        _persist_key(tmp_path)

        auth_file = tmp_path / ".factory" / ".bob_auth"
        assert auth_file.exists()
        assert auth_file.read_text() == "test-secret-key"

        # Verify file permissions (chmod 600)
        mode = auth_file.stat().st_mode
        assert mode & 0o777 == 0o600

    def test_persist_key_no_op_without_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify _persist_key does nothing if BOBSHELL_API_KEY is not set."""
        monkeypatch.delenv("BOBSHELL_API_KEY", raising=False)

        (tmp_path / ".factory").mkdir()

        from factory.runners.bob import _persist_key

        _persist_key(tmp_path)

        auth_file = tmp_path / ".factory" / ".bob_auth"
        assert not auth_file.exists()

    def test_check_auth_reads_from_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify _check_auth falls back to reading from file when env var missing."""
        monkeypatch.delenv("BOBSHELL_API_KEY", raising=False)

        import factory.runners.bob as bob_module
        bob_module._auth_checked = False

        # Create the auth file
        (tmp_path / ".factory").mkdir()
        auth_file = tmp_path / ".factory" / ".bob_auth"
        auth_file.write_text("file-based-key")

        # Change to tmp_path so _find_auth_file can find it
        monkeypatch.chdir(tmp_path)

        from factory.runners.bob import _check_auth

        _check_auth()

        # Verify the key was injected into os.environ
        assert os.environ.get("BOBSHELL_API_KEY") == "file-based-key"
        # Clean up injected env var
        monkeypatch.delenv("BOBSHELL_API_KEY", raising=False)
        bob_module._auth_checked = False

    def test_check_auth_prefers_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify env var takes precedence over file."""
        monkeypatch.setenv("BOBSHELL_API_KEY", "env-key")

        import factory.runners.bob as bob_module
        bob_module._auth_checked = False

        # Create the auth file with a different key
        (tmp_path / ".factory").mkdir()
        auth_file = tmp_path / ".factory" / ".bob_auth"
        auth_file.write_text("file-key")

        monkeypatch.chdir(tmp_path)

        from factory.runners.bob import _check_auth

        _check_auth()

        # Env var should still be the original value
        assert os.environ.get("BOBSHELL_API_KEY") == "env-key"
        bob_module._auth_checked = False

    def test_preflight_error_unchanged_when_no_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify BobAuthError is raised when key is missing from both env and file."""
        monkeypatch.delenv("BOBSHELL_API_KEY", raising=False)

        import factory.runners.bob as bob_module
        bob_module._auth_checked = False

        # No .factory directory, no auth file
        monkeypatch.chdir(tmp_path)

        from factory.runners.bob import _check_auth, BobAuthError

        with pytest.raises(BobAuthError) as exc_info:
            _check_auth()

        assert "BOBSHELL_API_KEY environment variable is not set" in str(exc_info.value)
        bob_module._auth_checked = False

    async def test_headless_passes_key_to_subprocess(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the subprocess env dict contains BOBSHELL_API_KEY from file."""
        monkeypatch.delenv("BOBSHELL_API_KEY", raising=False)
        monkeypatch.delenv("FACTORY_BOB_DRY_RUN", raising=False)

        import factory.runners.bob as bob_module
        bob_module._auth_checked = False

        # Create the auth file
        (tmp_path / ".factory").mkdir()
        auth_file = tmp_path / ".factory" / ".bob_auth"
        auth_file.write_text("subprocess-test-key")

        monkeypatch.chdir(tmp_path)

        with patch(
            "factory.runners.bob.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b"output", b"")

            with patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                runner = BobRunner()
                await runner.headless(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    role="researcher",
                )

                # Verify the subprocess was called with env containing the key
                call_kwargs = mock_exec.call_args.kwargs
                assert "env" in call_kwargs
                assert call_kwargs["env"].get("BOBSHELL_API_KEY") == "subprocess-test-key"

        monkeypatch.delenv("BOBSHELL_API_KEY", raising=False)
        bob_module._auth_checked = False


class TestStreamingOutput:
    """Tests for streaming subprocess output to terminal."""

    def test_should_stream_defaults_true_with_tty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """should_stream() returns True when stdout is a TTY and QUIET not set."""
        monkeypatch.delenv("FACTORY_RUNNER_QUIET", raising=False)

        from factory.runners._stream import should_stream

        # When stdout is a TTY, should return True
        with patch("sys.stdout.isatty", return_value=True):
            assert should_stream() is True

    def test_should_stream_false_when_quiet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """should_stream() returns False when FACTORY_RUNNER_QUIET=1."""
        monkeypatch.setenv("FACTORY_RUNNER_QUIET", "1")

        from factory.runners._stream import should_stream

        with patch("sys.stdout.isatty", return_value=True):
            assert should_stream() is False

    def test_should_stream_false_when_not_tty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """should_stream() returns False when stdout is not a TTY."""
        monkeypatch.delenv("FACTORY_RUNNER_QUIET", raising=False)

        from factory.runners._stream import should_stream

        with patch("sys.stdout.isatty", return_value=False):
            assert should_stream() is False

    async def test_tee_stream_collects_output(self) -> None:
        """tee_stream() collects all bytes in buffer."""
        from io import BytesIO

        from factory.runners._stream import tee_stream

        # Create a mock stream reader
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
        """tee_stream() writes to destination when stream=True."""
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
        """tee_stream() prepends prefix to each line when provided."""
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
        # Buffer should NOT have prefix — only raw output
        assert buffer == [b"line1\n", b"line2\n"]

    async def test_stream_subprocess_collects_both_streams(self) -> None:
        """stream_subprocess() collects from both stdout and stderr."""
        from factory.runners._stream import stream_subprocess

        # Create mock process with mock streams
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
        """ClaudeRunner.headless() streams output when should_stream() is True."""
        monkeypatch.delenv("FACTORY_RUNNER_QUIET", raising=False)

        runner = ClaudeRunner()

        # Mock should_stream to return True
        with patch("factory.runners.claude.should_stream", return_value=True):
            with patch(
                "factory.runners.claude.stream_subprocess", new_callable=AsyncMock
            ) as mock_stream:
                mock_stream.return_value = (b"output\n", b"")

                with patch(
                    "asyncio.create_subprocess_exec", new_callable=AsyncMock
                ) as mock_exec:
                    mock_proc = AsyncMock()
                    mock_proc.returncode = 0
                    mock_exec.return_value = mock_proc

                    stdout, code = await runner.headless(
                        prompt="Test",
                        task="Test",
                        cwd=tmp_path,
                        role="researcher",
                    )

                    # Verify stream_subprocess was called with streaming enabled
                    mock_stream.assert_called_once()
                    call_kwargs = mock_stream.call_args.kwargs
                    assert call_kwargs["stream"] is True
                    assert call_kwargs["prefix"] == "[claude:researcher]"

    async def test_bob_runner_uses_streaming(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BobRunner.headless() streams output when should_stream() is True."""
        monkeypatch.setenv("FACTORY_BOB_DRY_RUN", "1")
        monkeypatch.delenv("FACTORY_RUNNER_QUIET", raising=False)

        (tmp_path / ".factory").mkdir()

        runner = BobRunner()

        # For dry-run, streaming doesn't apply — test the non-dry-run path
        monkeypatch.delenv("FACTORY_BOB_DRY_RUN", raising=False)
        monkeypatch.setenv("BOBSHELL_API_KEY", "test-key")

        import factory.runners.bob as bob_module
        bob_module._auth_checked = False

        with patch("factory.runners.bob.should_stream", return_value=True):
            with patch(
                "factory.runners.bob.stream_subprocess", new_callable=AsyncMock
            ) as mock_stream:
                mock_stream.return_value = (b"output\n", b"")

                with patch(
                    "asyncio.create_subprocess_exec", new_callable=AsyncMock
                ) as mock_exec:
                    mock_proc = AsyncMock()
                    mock_proc.returncode = 0
                    mock_exec.return_value = mock_proc

                    stdout, code = await runner.headless(
                        prompt="Test",
                        task="Test",
                        cwd=tmp_path,
                        role="builder",
                    )

                    # Verify stream_subprocess was called with streaming enabled
                    mock_stream.assert_called_once()
                    call_kwargs = mock_stream.call_args.kwargs
                    assert call_kwargs["stream"] is True
                    assert call_kwargs["prefix"] == "[bob:builder]"

        bob_module._auth_checked = False

    async def test_quiet_mode_disables_streaming(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FACTORY_RUNNER_QUIET=1 disables streaming to terminal."""
        monkeypatch.setenv("FACTORY_RUNNER_QUIET", "1")

        runner = ClaudeRunner()

        with patch(
            "factory.runners.claude.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b"output\n", b"")

            with patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                await runner.headless(
                    prompt="Test",
                    task="Test",
                    cwd=tmp_path,
                    role="researcher",
                )

                # Verify stream_subprocess was called with streaming disabled
                mock_stream.assert_called_once()
                call_kwargs = mock_stream.call_args.kwargs
                assert call_kwargs["stream"] is False

    async def test_output_saved_to_review_file_matches_buffer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The saved review file contains the same content as the buffer."""
        monkeypatch.delenv("FACTORY_RUNNER_QUIET", raising=False)

        (tmp_path / ".factory" / "reviews").mkdir(parents=True)

        # Import invoke_agent which saves the review
        from factory.agents.runner import invoke_agent

        expected_output = "Line 1\nLine 2\nLine 3\n"

        with patch(
            "factory.runners.claude.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (expected_output.encode(), b"")

            with patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
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

                assert expected_output in stdout

                # Check the saved review file
                review_file = tmp_path / ".factory" / "reviews" / "researcher-latest.md"
                assert review_file.exists()
                content = review_file.read_text()
                assert "Line 1" in content
                assert "Line 2" in content
                assert "Line 3" in content


class TestCeilingAccumulationAcrossInvocations:
    """Tests that per-cycle ceiling accumulates across invoke_agent calls."""

    async def test_ceiling_accumulates_across_invoke_agent_calls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that invocation counts accumulate across multiple invoke_agent calls.

        This test reproduces the bug from PR #136: each get_runner() call created
        a fresh BobRunner with cycle_start=now(), so the ceiling never accumulated.

        With the fix, get_runner() passes project_path to BobRunner, which reads
        started_at from .factory/state/cycle.json, ensuring all invocations within
        a cycle share the same cycle_start and accumulate correctly.
        """
        from unittest.mock import AsyncMock, patch

        from factory.agents.runner import invoke_agent
        from factory.ceo_completion import write_cycle_state, create_cycle_state

        monkeypatch.setenv("FACTORY_RUNNER", "bob")
        monkeypatch.setenv("BOBSHELL_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_BOB_DRY_RUN", raising=False)
        monkeypatch.setenv("FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE", "2")

        # Reset auth check state
        import factory.runners.bob as bob_module
        bob_module._auth_checked = False

        # Create project structure
        (tmp_path / ".factory").mkdir()
        (tmp_path / ".factory" / "state").mkdir()

        # Create a cycle state (simulates an in-flight cycle)
        cycle_state = create_cycle_state("improve", "test task", "bob")
        write_cycle_state(tmp_path, cycle_state)

        # Create a minimal agent prompt
        prompts_dir = tmp_path / ".factory" / "agents"
        prompts_dir.mkdir()
        (prompts_dir / "researcher.md").write_text("You are a researcher.")

        # Mock subprocess to avoid actually calling bob
        with patch(
            "factory.runners.bob.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b"output", b"")

            with patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                # First invocation — should succeed (1/2)
                stdout1, code1 = await invoke_agent(
                    "researcher",
                    "First task",
                    tmp_path,
                    runner_name="bob",
                )
                assert code1 == 0, f"First invocation failed: {stdout1}"

                # Second invocation — should succeed (2/2)
                stdout2, code2 = await invoke_agent(
                    "researcher",
                    "Second task",
                    tmp_path,
                    runner_name="bob",
                )
                assert code2 == 0, f"Second invocation failed: {stdout2}"

                # Third invocation — should fail (3/2 = ceiling exceeded)
                stdout3, code3 = await invoke_agent(
                    "researcher",
                    "Third task",
                    tmp_path,
                    runner_name="bob",
                )
                assert code3 == 1, "Third invocation should have hit the ceiling"
                assert "ceiling" in stdout3.lower() or "exceeded" in stdout3.lower()

        bob_module._auth_checked = False

    async def test_bobrunner_reads_cycle_start_from_cycle_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify BobRunner reads started_at from cycle.json when project_path is provided."""

        from factory.ceo_completion import write_cycle_state, create_cycle_state
        from factory.runners import get_runner

        monkeypatch.setenv("FACTORY_BOB_DRY_RUN", "1")

        # Create project structure
        (tmp_path / ".factory").mkdir()
        (tmp_path / ".factory" / "state").mkdir()

        # Create a cycle state with a known started_at
        cycle_state = create_cycle_state("improve", "test task", "bob")
        write_cycle_state(tmp_path, cycle_state)

        # Get runner with project_path
        runner = get_runner("bob", project_path=tmp_path)

        # Runner's cycle_start should match the persisted state's started_at
        # (allowing for small time differences in serialization)
        time_diff = abs((runner.cycle_start - cycle_state.started_at).total_seconds())
        assert time_diff < 1.0, f"cycle_start mismatch: {runner.cycle_start} vs {cycle_state.started_at}"

    async def test_bobrunner_falls_back_to_now_without_cycle_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify BobRunner falls back to now() when no cycle.json exists."""
        from datetime import datetime, timezone

        from factory.runners import get_runner

        monkeypatch.setenv("FACTORY_BOB_DRY_RUN", "1")

        # Create project structure but NO cycle.json
        (tmp_path / ".factory").mkdir()

        now_before = datetime.now(timezone.utc)

        # Get runner with project_path (but no cycle.json exists)
        runner = get_runner("bob", project_path=tmp_path)

        now_after = datetime.now(timezone.utc)

        # Runner's cycle_start should be between now_before and now_after
        assert now_before <= runner.cycle_start <= now_after
