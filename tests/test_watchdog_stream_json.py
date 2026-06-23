"""Regression tests for stream-json watchdog fix (issue #712, PR #724).

Verifies that:
1. The watchdog does NOT kill processes emitting periodic JSONL output
2. The watchdog DOES kill processes with zero output
3. headless() correctly parses JSONL stream into result_text and usage
4. build_command uses stream-json AND --verbose (both required with -p)
5. Non-JSON lines in the stream are gracefully skipped
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from factory.models import AgentRunRequest
from factory.runners import ClaudeRunner


class TestWatchdogStreamJson:
    """Verify watchdog behavior with stream-json JSONL output."""

    async def test_periodic_jsonl_output_prevents_watchdog_kill(self) -> None:
        """A process emitting periodic JSONL lines is NOT killed by the watchdog."""
        script = (
            "import time, json, sys\n"
            "for i in range(6):\n"
            "    print(json.dumps({'type': 'progress', 'step': i}), flush=True)\n"
            "    time.sleep(0.3)\n"
            "print(json.dumps({'result': 'done', 'usage': {}}), flush=True)\n"
        )
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        from factory.runners._stream import stream_subprocess

        stdout, stderr = await stream_subprocess(
            proc, stream=False, inactivity_timeout=1.0,
        )

        assert proc.returncode == 0
        lines = stdout.decode().strip().splitlines()
        assert len(lines) == 7
        last = json.loads(lines[-1])
        assert last["result"] == "done"

    async def test_silent_process_is_killed_by_watchdog(self) -> None:
        """A process emitting zero output for >timeout seconds IS killed."""
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", "import time; time.sleep(60)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        from factory.runners._stream import stream_subprocess

        killed_by_watchdog: list[bool] = [False]
        stdout, stderr = await stream_subprocess(
            proc, stream=False, inactivity_timeout=0.5,
            killed_by_watchdog=killed_by_watchdog,
        )

        assert proc.returncode == -9
        assert killed_by_watchdog[0] is True


class TestHeadlessJsonlParsing:
    """Verify headless() correctly parses JSONL stream output."""

    async def test_parses_final_result_message(self, tmp_path: Path) -> None:
        """headless() extracts result text and usage from the last JSONL result line."""
        jsonl_lines = [
            json.dumps({"type": "assistant", "message": "thinking..."}),
            json.dumps({"type": "tool_use", "name": "Bash"}),
            json.dumps({
                "result": "Build succeeded",
                "usage": {
                    "input_tokens": 1500,
                    "output_tokens": 300,
                    "cache_read_input_tokens": 50,
                    "cache_creation_input_tokens": 10,
                },
                "total_cost_usd": 0.05,
                "duration_ms": 12000,
                "num_turns": 3,
                "model": "claude-sonnet-4-6",
                "session_id": "sess-123",
                "uuid": "uuid-456",
            }),
        ]
        stdout_bytes = ("\n".join(jsonl_lines) + "\n").encode()

        runner = ClaudeRunner()

        with patch(
            "factory.runners._subprocess.stream_subprocess", new_callable=AsyncMock,
        ) as mock_stream:
            mock_stream.return_value = (stdout_bytes, b"")

            with patch(
                "factory.runners._subprocess.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                result = await runner.headless(AgentRunRequest(
                    prompt="Test agent",
                    task="Build the project",
                    cwd=tmp_path,
                    timeout=60.0,
                    model="claude-sonnet-4-6",
                ))

        assert result.return_code == 0
        assert result.stdout == "Build succeeded"
        assert result.usage is not None
        assert result.usage.input_tokens == 1500
        assert result.usage.output_tokens == 300
        assert result.usage.cache_read_tokens == 50
        assert result.usage.cache_creation_tokens == 10
        assert result.usage.total_cost_usd == 0.05
        assert result.usage.duration_ms == 12000
        assert result.usage.num_turns == 3
        assert result.usage.model == "claude-sonnet-4-6"
        assert result.metadata.get("session_id") == "sess-123"
        assert result.metadata.get("uuid") == "uuid-456"

    async def test_no_result_line_falls_back_to_raw_stdout(self, tmp_path: Path) -> None:
        """When no JSONL line has a 'result' key, headless() returns raw stdout."""
        jsonl_lines = [
            json.dumps({"type": "assistant", "message": "hello"}),
            json.dumps({"type": "tool_use", "name": "Read"}),
        ]
        stdout_bytes = ("\n".join(jsonl_lines) + "\n").encode()

        runner = ClaudeRunner()

        with patch(
            "factory.runners._subprocess.stream_subprocess", new_callable=AsyncMock,
        ) as mock_stream:
            mock_stream.return_value = (stdout_bytes, b"")

            with patch(
                "factory.runners._subprocess.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                result = await runner.headless(AgentRunRequest(
                    prompt="Test agent",
                    task="Do something",
                    cwd=tmp_path,
                ))

        assert result.stdout == stdout_bytes.decode()
        assert result.usage is None

    async def test_non_json_lines_are_skipped(self, tmp_path: Path) -> None:
        """Non-JSON lines in the stream are gracefully skipped."""
        lines = [
            "Some plain text warning",
            json.dumps({"type": "progress"}),
            "Another non-json line",
            json.dumps({
                "result": "final answer",
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "total_cost_usd": 0.01,
                "duration_ms": 5000,
                "num_turns": 1,
                "model": "claude-sonnet-4-6",
            }),
        ]
        stdout_bytes = ("\n".join(lines) + "\n").encode()

        runner = ClaudeRunner()

        with patch(
            "factory.runners._subprocess.stream_subprocess", new_callable=AsyncMock,
        ) as mock_stream:
            mock_stream.return_value = (stdout_bytes, b"")

            with patch(
                "factory.runners._subprocess.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                result = await runner.headless(AgentRunRequest(
                    prompt="Test", task="Test", cwd=tmp_path,
                ))

        assert result.stdout == "final answer"
        assert result.usage is not None
        assert result.usage.input_tokens == 100


class TestBuildCommandFlags:
    """Verify build_command() emits the correct CLI flags."""

    async def test_build_command_uses_stream_json(self, tmp_path: Path) -> None:
        """build_command() emits --output-format stream-json."""
        runner = ClaudeRunner()
        cmd, _, temp_files = runner.build_command(AgentRunRequest(
            prompt="Test", task="Test", cwd=tmp_path,
        ))

        try:
            fmt_idx = cmd.index("--output-format")
            assert cmd[fmt_idx + 1] == "stream-json"
        finally:
            for f in temp_files:
                f.unlink(missing_ok=True)

    async def test_build_command_includes_verbose(self, tmp_path: Path) -> None:
        """build_command() includes --verbose (required with -p and stream-json)."""
        runner = ClaudeRunner()
        cmd, _, temp_files = runner.build_command(AgentRunRequest(
            prompt="Test", task="Test", cwd=tmp_path,
        ))

        try:
            assert "--verbose" in cmd
        finally:
            for f in temp_files:
                f.unlink(missing_ok=True)
