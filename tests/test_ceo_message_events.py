"""Tests for ceo.message event emission from ClaudeRunner streaming."""

from __future__ import annotations

import json
from pathlib import Path

from factory.runners.claude import _make_ceo_message_emitter


class TestMakeCeoMessageEmitter:
    def test_emits_event_for_assistant_message(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        emitter = _make_ceo_message_emitter(project)
        line = json.dumps({"type": "assistant", "message": "Here is my plan..."}).encode()
        emitter(line)

        events_file = project / ".factory" / "events.jsonl"
        assert events_file.exists()
        events = [json.loads(ln) for ln in events_file.read_text().strip().splitlines()]
        assert len(events) == 1
        assert events[0]["type"] == "ceo.message"
        assert events[0]["agent"] == "ceo"
        assert events[0]["data"]["message"] == "Here is my plan..."
        assert events[0]["data"]["message_type"] == "assistant"

    def test_ignores_non_assistant_types(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        emitter = _make_ceo_message_emitter(project)
        for line_data in [
            {"type": "system", "message": "starting"},
            {"type": "tool_use", "name": "Bash"},
            {"type": "result", "result": "done"},
        ]:
            emitter(json.dumps(line_data).encode())

        events_file = project / ".factory" / "events.jsonl"
        assert not events_file.exists()

    def test_ignores_non_json_lines(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        emitter = _make_ceo_message_emitter(project)
        emitter(b"not json at all\n")
        emitter(b"\n")
        emitter(b"")

        events_file = project / ".factory" / "events.jsonl"
        assert not events_file.exists()

    def test_preserves_long_messages(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        emitter = _make_ceo_message_emitter(project)
        long_msg = "x" * 5000
        line = json.dumps({"type": "assistant", "message": long_msg}).encode()
        emitter(line)

        events_file = project / ".factory" / "events.jsonl"
        event = json.loads(events_file.read_text().strip())
        assert event["data"]["message"] == long_msg

    def test_skips_empty_message(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        emitter = _make_ceo_message_emitter(project)
        emitter(json.dumps({"type": "assistant", "message": ""}).encode())

        events_file = project / ".factory" / "events.jsonl"
        assert not events_file.exists()

    def test_skips_non_string_message(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        emitter = _make_ceo_message_emitter(project)
        emitter(json.dumps({"type": "assistant", "message": 42}).encode())

        events_file = project / ".factory" / "events.jsonl"
        assert not events_file.exists()

    def test_multiple_messages_append(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        emitter = _make_ceo_message_emitter(project)
        emitter(json.dumps({"type": "assistant", "message": "msg1"}).encode())
        emitter(json.dumps({"type": "assistant", "message": "msg2"}).encode())

        events_file = project / ".factory" / "events.jsonl"
        events = [json.loads(ln) for ln in events_file.read_text().strip().splitlines()]
        assert len(events) == 2
        assert events[0]["data"]["message"] == "msg1"
        assert events[1]["data"]["message"] == "msg2"


class TestCeoCallbackWiring:
    """Verify that ClaudeRunner.headless() only wires the callback for CEO role."""

    async def test_ceo_role_gets_callback(self, tmp_path: Path) -> None:
        """The on_line callback should be constructed for role='ceo'."""
        from unittest.mock import AsyncMock, patch

        from factory.models import AgentRunRequest, AgentRunResult
        from factory.runners.claude import ClaudeRunner

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        request = AgentRunRequest(
            prompt="test prompt",
            task="test task",
            cwd=project,
            role="ceo",
            project_path=project,
        )

        mock_result = AgentRunResult(stdout="", return_code=0)

        with patch("factory.runners.claude.run_subprocess", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            runner = ClaudeRunner()
            await runner.headless(request)

            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["on_line"] is not None

    async def test_non_ceo_role_no_callback(self, tmp_path: Path) -> None:
        """Non-CEO roles should not get an on_line callback."""
        from unittest.mock import AsyncMock, patch

        from factory.models import AgentRunRequest, AgentRunResult
        from factory.runners.claude import ClaudeRunner

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        request = AgentRunRequest(
            prompt="test prompt",
            task="test task",
            cwd=project,
            role="builder",
            project_path=project,
        )

        mock_result = AgentRunResult(stdout="", return_code=0)

        with patch("factory.runners.claude.run_subprocess", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            runner = ClaudeRunner()
            await runner.headless(request)

            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["on_line"] is None

    async def test_ceo_without_project_path_no_callback(self, tmp_path: Path) -> None:
        """CEO role without project_path should not get an on_line callback."""
        from unittest.mock import AsyncMock, patch

        from factory.models import AgentRunRequest, AgentRunResult
        from factory.runners.claude import ClaudeRunner

        request = AgentRunRequest(
            prompt="test prompt",
            task="test task",
            cwd=tmp_path,
            role="ceo",
            project_path=None,
        )

        mock_result = AgentRunResult(stdout="", return_code=0)

        with patch("factory.runners.claude.run_subprocess", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            runner = ClaudeRunner()
            await runner.headless(request)

            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["on_line"] is None


class TestTeeStreamOnLineCallback:
    """Test that tee_stream invokes the on_line callback for each line."""

    async def test_on_line_callback_invoked_for_each_line(self) -> None:
        """The on_line callback is called with raw bytes for each line."""
        import asyncio
        import io

        from factory.runners._stream import tee_stream

        # Create a mock stream reader with test data
        lines = [b"line one\n", b"line two\n", b"line three\n"]
        reader = asyncio.StreamReader()
        for line in lines:
            reader.feed_data(line)
        reader.feed_eof()

        buffer: list[bytes] = []
        captured_lines: list[bytes] = []

        def on_line_callback(line: bytes) -> None:
            captured_lines.append(line)

        dest = io.BytesIO()

        await tee_stream(
            reader,
            dest,
            buffer,
            stream=False,
            on_line=on_line_callback,
        )

        assert len(captured_lines) == 3
        assert captured_lines[0] == b"line one\n"
        assert captured_lines[1] == b"line two\n"
        assert captured_lines[2] == b"line three\n"
        assert buffer == captured_lines  # Buffer should match

    async def test_on_line_none_does_not_crash(self) -> None:
        """When on_line is None, tee_stream works normally without calling anything."""
        import asyncio
        import io

        from factory.runners._stream import tee_stream

        reader = asyncio.StreamReader()
        reader.feed_data(b"test line\n")
        reader.feed_eof()

        buffer: list[bytes] = []
        dest = io.BytesIO()

        await tee_stream(reader, dest, buffer, stream=False, on_line=None)

        assert len(buffer) == 1
        assert buffer[0] == b"test line\n"
