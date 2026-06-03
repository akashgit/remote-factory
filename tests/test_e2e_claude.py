"""End-to-end tests for ClaudeRunner with real Claude Code CLI.

These tests spawn actual `claude` subprocesses and cost tokens.
Run explicitly with: uv run pytest tests/test_e2e_claude.py -v -m e2e

Prerequisites:
- Claude Code CLI installed and authenticated
- Active subscription or API key
"""

from __future__ import annotations

import shutil

import pytest

from factory.runners.claude import ClaudeRunner
from factory.runners.types import (
    PermissionMode,
    RunnerRequest,
    RunnerResponse,
)

pytestmark = pytest.mark.e2e

# Skip entire module if claude CLI not available
if not shutil.which("claude"):
    pytest.skip("claude CLI not found", allow_module_level=True)


@pytest.fixture
def runner():
    return ClaudeRunner()


@pytest.fixture
def tmp_cwd(tmp_path):
    """Provide a temp directory as working directory for agent invocations."""
    return str(tmp_path)


class TestHealthCheck:
    async def test_check_health(self, runner):
        healthy, msg = await runner.check_health()
        assert healthy is True
        assert "claude" in msg


class TestBasicHeadless:
    async def test_v2_request_returns_response(self, runner, tmp_cwd):
        """v2 RunnerRequest → RunnerResponse with trace and usage."""
        request = RunnerRequest(
            system_prompt="You are a helpful assistant. Reply concisely.",
            task="Reply with exactly: HELLO",
            cwd=tmp_cwd,
            timeout=60,
            max_turns=1,
        )
        response = await runner.headless(request)
        assert isinstance(response, RunnerResponse)
        assert response.exit_code == 0
        assert "HELLO" in response.output

    async def test_usage_stats_populated(self, runner, tmp_cwd):
        """Usage stats should be parsed from stream-json output."""
        request = RunnerRequest(
            system_prompt="Reply concisely.",
            task="What is 2+2? Reply with just the number.",
            cwd=tmp_cwd,
            timeout=60,
            max_turns=1,
        )
        response = await runner.headless(request)
        assert response.usage is not None
        assert response.usage.input_tokens is not None
        assert response.usage.input_tokens > 0
        assert response.usage.output_tokens is not None
        assert response.usage.output_tokens > 0

    async def test_execution_trace_populated(self, runner, tmp_cwd):
        """Execution trace should have at least one step."""
        request = RunnerRequest(
            system_prompt="Reply concisely.",
            task="Say OK.",
            cwd=tmp_cwd,
            timeout=60,
            max_turns=1,
        )
        response = await runner.headless(request)
        assert response.trace is not None
        assert len(response.trace.steps) >= 1


class TestSystemPrompt:
    async def test_system_prompt_separation(self, runner, tmp_cwd):
        """System prompt delivered via --append-system-prompt-file, task via -p."""
        request = RunnerRequest(
            system_prompt="You are a calculator. Only output numbers, nothing else.",
            task="What is 7 * 8?",
            cwd=tmp_cwd,
            timeout=60,
            max_turns=1,
        )
        response = await runner.headless(request)
        assert "56" in response.output

    async def test_append_system_prompt(self, runner, tmp_cwd):
        """Appended system prompt sections should be included."""
        request = RunnerRequest(
            system_prompt="You are a helpful assistant.",
            task="What is the secret word?",
            cwd=tmp_cwd,
            timeout=60,
            max_turns=1,
        )
        request.append_system_prompt("IMPORTANT: The secret word is BANANA. When asked, reply with it.")
        response = await runner.headless(request)
        assert "BANANA" in response.output


class TestToolControl:
    async def test_allowed_tools_restricts(self, runner, tmp_cwd):
        """With allowed_tools set, --allowedTools flag should be in command."""
        request = RunnerRequest(
            system_prompt="Reply concisely.",
            task="Say OK.",
            cwd=tmp_cwd,
            timeout=60,
            max_turns=1,
            allowed_tools=["Read", "Grep", "Glob"],
        )
        # Verify the command includes --allowedTools
        cmd = runner._build_command(request)
        assert "--allowedTools" in cmd
        idx = cmd.index("--allowedTools")
        assert cmd[idx + 1] == "Read,Grep,Glob"


class TestPermissionMode:
    def test_auto_adds_skip_flag(self, runner):
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            permission_mode=PermissionMode.AUTO,
        )
        cmd = runner._build_command(request)
        assert "--dangerously-skip-permissions" in cmd

    def test_approve_writes_no_skip_flag(self, runner):
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            permission_mode=PermissionMode.APPROVE_WRITES,
            skip_permissions=False,
        )
        cmd = runner._build_command(request)
        assert "--dangerously-skip-permissions" not in cmd


class TestMaxTurns:
    def test_max_turns_in_command(self, runner):
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            max_turns=3,
        )
        cmd = runner._build_command(request)
        assert "--max-turns" in cmd
        idx = cmd.index("--max-turns")
        assert cmd[idx + 1] == "3"


class TestSessionResume:
    async def test_session_id_returned(self, runner, tmp_cwd):
        """Session ID should be returned from stream-json output."""
        request = RunnerRequest(
            system_prompt="Reply concisely.",
            task="Say OK.",
            cwd=tmp_cwd,
            timeout=60,
            max_turns=1,
            session_name="test-e2e-session",
        )
        response = await runner.headless(request)
        # Session ID should be populated from the system event
        assert response.session_id is not None
