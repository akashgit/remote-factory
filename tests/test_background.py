"""Tests for the background agent execution module (factory/runners/_background.py)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from factory.runners._background import (
    _parse_bg_session_id,
    _read_session_state,
    _unlink_quiet,
    run_in_background,
)
from factory.runners.claude import ClaudeRunner


class TestUnlinkQuiet:
    def test_removes_existing_file(self, tmp_path: Path) -> None:
        f = tmp_path / "tempfile"
        f.write_text("data")
        _unlink_quiet(str(f))
        assert not f.exists()

    def test_no_error_on_missing_file(self, tmp_path: Path) -> None:
        _unlink_quiet(str(tmp_path / "nonexistent"))


class TestReadSessionState:
    def test_returns_none_for_missing_path(self, tmp_path: Path) -> None:
        with patch("factory.runners._background._CLAUDE_JOBS_DIR", tmp_path):
            result = _read_session_state("nonexistent-id")
        assert result is None

    def test_returns_none_for_invalid_json(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "abc123"
        session_dir.mkdir()
        (session_dir / "state.json").write_text("not valid json {{{")
        with patch("factory.runners._background._CLAUDE_JOBS_DIR", tmp_path):
            result = _read_session_state("abc123")
        assert result is None

    def test_returns_dict_for_valid_json(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "abc123"
        session_dir.mkdir()
        state = {"state": "done", "output": {"result": "ok"}}
        (session_dir / "state.json").write_text(json.dumps(state))
        with patch("factory.runners._background._CLAUDE_JOBS_DIR", tmp_path):
            result = _read_session_state("abc123")
        assert result == state


class TestParseBgSessionId:
    def test_parses_standard_format(self) -> None:
        output = "backgrounded · abc123def · factory-ceo"
        assert _parse_bg_session_id(output) == "abc123def"

    def test_parses_without_name(self) -> None:
        output = "backgrounded · abc123def"
        assert _parse_bg_session_id(output) == "abc123def"

    def test_returns_none_for_unrecognized(self) -> None:
        assert _parse_bg_session_id("some other output") is None


class TestRunInBackground:
    async def test_success(self, tmp_path: Path) -> None:
        state = {"state": "done", "output": {"result": "task completed"}}

        with (
            patch("factory.runners._background.subprocess.run") as mock_run,
            patch("factory.runners._background._read_session_state") as mock_state,
            patch("factory.runners._background.asyncio.sleep", new_callable=AsyncMock),
            patch("factory.runners._background._unlink_quiet"),
        ):
            mock_run.return_value = MagicMock(
                returncode=0, stdout="backgrounded · abc123 · factory-ceo", stderr="",
            )
            mock_state.return_value = state

            stdout, rc, usage = await run_in_background(
                "prompt", "task", tmp_path, "researcher",
            )

        assert rc == 0
        assert stdout == "task completed"
        assert usage is None

    async def test_launch_failure(self, tmp_path: Path) -> None:
        with (
            patch("factory.runners._background.subprocess.run") as mock_run,
            patch("factory.runners._background._unlink_quiet"),
        ):
            mock_run.return_value = MagicMock(
                returncode=1, stdout="error occurred", stderr="",
            )

            stdout, rc, usage = await run_in_background(
                "prompt", "task", tmp_path, "researcher",
            )

        assert rc == 1
        assert "Failed" in stdout

    async def test_file_not_found(self, tmp_path: Path) -> None:
        with (
            patch("factory.runners._background.subprocess.run", side_effect=FileNotFoundError),
            patch("factory.runners._background._unlink_quiet"),
        ):
            stdout, rc, usage = await run_in_background(
                "prompt", "task", tmp_path, "researcher",
            )

        assert rc == 1
        assert "not found" in stdout.lower()

    async def test_launch_timeout(self, tmp_path: Path) -> None:
        import subprocess

        with (
            patch("factory.runners._background.subprocess.run") as mock_run,
            patch("factory.runners._background._unlink_quiet"),
        ):
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=30)

            stdout, rc, usage = await run_in_background(
                "prompt", "task", tmp_path, "researcher",
            )

        assert rc == 1
        assert "timed out" in stdout.lower()

    async def test_timeout_during_poll(self, tmp_path: Path) -> None:
        with (
            patch("factory.runners._background.subprocess.run") as mock_run,
            patch("factory.runners._background._read_session_state", return_value=None),
            patch("factory.runners._background.asyncio.sleep", new_callable=AsyncMock),
            patch("factory.runners._background._unlink_quiet"),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="backgrounded · abc123", stderr=""),
                MagicMock(returncode=0),  # claude stop
            ]

            stdout, rc, usage = await run_in_background(
                "prompt", "task", tmp_path, "researcher", timeout=0.01,
            )

        assert rc == 1
        assert "timed out" in stdout.lower()

    async def test_uses_prompt_file(self, tmp_path: Path) -> None:
        state = {"state": "done", "output": "ok"}

        captured_cmd: list[str] = []

        def mock_subprocess_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return MagicMock(
                returncode=0, stdout="backgrounded · abc123", stderr="",
            )

        with (
            patch("factory.runners._background.subprocess.run", side_effect=mock_subprocess_run),
            patch("factory.runners._background._read_session_state", return_value=state),
            patch("factory.runners._background.asyncio.sleep", new_callable=AsyncMock),
            patch("factory.runners._background._unlink_quiet"),
        ):
            await run_in_background(
                "my system prompt", "do stuff", tmp_path, "builder",
            )

        assert "--append-system-prompt-file" in captured_cmd

    async def test_cleanup_on_success(self, tmp_path: Path) -> None:
        state = {"state": "done", "output": "ok"}
        unlinked_paths: list[str] = []

        def track_unlink(path: str) -> None:
            unlinked_paths.append(path)

        with (
            patch("factory.runners._background.subprocess.run") as mock_run,
            patch("factory.runners._background._read_session_state", return_value=state),
            patch("factory.runners._background.asyncio.sleep", new_callable=AsyncMock),
            patch("factory.runners._background._unlink_quiet", side_effect=track_unlink),
        ):
            mock_run.return_value = MagicMock(
                returncode=0, stdout="backgrounded · abc123", stderr="",
            )

            await run_in_background(
                "prompt", "task", tmp_path, "researcher",
            )

        assert len(unlinked_paths) == 1

    async def test_output_from_string(self, tmp_path: Path) -> None:
        state = {"state": "completed", "output": "plain string output"}

        with (
            patch("factory.runners._background.subprocess.run") as mock_run,
            patch("factory.runners._background._read_session_state", return_value=state),
            patch("factory.runners._background.asyncio.sleep", new_callable=AsyncMock),
            patch("factory.runners._background._unlink_quiet"),
        ):
            mock_run.return_value = MagicMock(
                returncode=0, stdout="backgrounded · abc123", stderr="",
            )

            stdout, rc, _ = await run_in_background(
                "prompt", "task", tmp_path, "researcher",
            )

        assert rc == 0
        assert stdout == "plain string output"

    async def test_failed_state_returns_error_code(self, tmp_path: Path) -> None:
        state = {"state": "failed", "output": {"result": "error detail"}}

        with (
            patch("factory.runners._background.subprocess.run") as mock_run,
            patch("factory.runners._background._read_session_state", return_value=state),
            patch("factory.runners._background.asyncio.sleep", new_callable=AsyncMock),
            patch("factory.runners._background._unlink_quiet"),
        ):
            mock_run.return_value = MagicMock(
                returncode=0, stdout="backgrounded · abc123", stderr="",
            )

            stdout, rc, _ = await run_in_background(
                "prompt", "task", tmp_path, "researcher",
            )

        assert rc == 1
        assert stdout == "error detail"

    async def test_no_session_id_in_output(self, tmp_path: Path) -> None:
        with (
            patch("factory.runners._background.subprocess.run") as mock_run,
            patch("factory.runners._background._unlink_quiet"),
        ):
            mock_run.return_value = MagicMock(
                returncode=0, stdout="unexpected output format", stderr="",
            )

            stdout, rc, _ = await run_in_background(
                "prompt", "task", tmp_path, "researcher",
            )

        assert rc == 1
        assert "Failed" in stdout


class TestClaudeRunnerBackgroundDispatch:
    async def test_headless_routes_to_run_in_background(self, tmp_path: Path) -> None:
        from factory.models import AgentRunRequest

        runner = ClaudeRunner()

        request = AgentRunRequest(
            prompt="test prompt", task="test task", cwd=tmp_path,
            role="researcher", extras={"background": True},
        )

        with patch(
            "factory.runners._background.run_in_background",
            new_callable=AsyncMock,
            return_value=("bg output", 0, None),
        ) as mock_bg:
            result = await runner.headless(request)

            assert result.stdout == "bg output"
            assert result.return_code == 0
            mock_bg.assert_called_once()
