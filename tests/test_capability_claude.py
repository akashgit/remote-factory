"""Per-capability tests for ClaudeRunner using REAL Claude Code CLI.

Each test exercises ONE capability through the v2 CLIAdapter.headless() path.
Uses max_turns=1 and simple tasks to minimize token usage.

Run: uv run pytest tests/test_capability_claude.py -v -m e2e --timeout=120
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


# ---------------------------------------------------------------------------
# 1. Health Check
# ---------------------------------------------------------------------------

class TestClaudeHealthCheck:
    async def test_check_health(self, runner):
        healthy, msg = await runner.check_health()
        assert healthy is True
        assert "claude found" in msg


# ---------------------------------------------------------------------------
# 2. Basic Headless
# ---------------------------------------------------------------------------

class TestClaudeBasicHeadless:
    async def test_v2_request_returns_runner_response(self, runner, tmp_cwd):
        """v2 RunnerRequest -> RunnerResponse (not tuple)."""
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
        assert len(response.output) > 0


# ---------------------------------------------------------------------------
# 3. Usage Stats
# ---------------------------------------------------------------------------

class TestClaudeUsageStats:
    async def test_usage_tokens_populated(self, runner, tmp_cwd):
        """usage.input_tokens > 0 and usage.output_tokens > 0 after a real call."""
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


# ---------------------------------------------------------------------------
# 4. Execution Trace
# ---------------------------------------------------------------------------

class TestClaudeExecutionTrace:
    async def test_trace_has_steps(self, runner, tmp_cwd):
        """trace has >= 1 step after a real call."""
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


# ---------------------------------------------------------------------------
# 5. System Prompt
# ---------------------------------------------------------------------------

class TestClaudeSystemPrompt:
    async def test_system_prompt_delivered(self, runner, tmp_cwd):
        """System prompt delivered via temp file + --append-system-prompt-file."""
        request = RunnerRequest(
            system_prompt="The secret word is BANANA. When asked for the secret word, reply with BANANA.",
            task="What is the secret word?",
            cwd=tmp_cwd,
            timeout=60,
            max_turns=1,
        )
        response = await runner.headless(request)
        assert "BANANA" in response.output


# ---------------------------------------------------------------------------
# 6. Append System Prompt
# ---------------------------------------------------------------------------

class TestClaudeAppendSystemPrompt:
    def test_append_system_prompt_in_full_prompt(self):
        """request.append_system_prompt() adds content to full_system_prompt."""
        request = RunnerRequest(
            system_prompt="Base prompt.",
            task="Do something.",
            cwd="/tmp",
        )
        request.append_system_prompt("APPENDED SECTION ALPHA")
        assert "APPENDED SECTION ALPHA" in request.full_system_prompt

    def test_append_system_prompt_in_temp_file(self, runner):
        """Appended content is written to the temp file used by _write_system_prompt."""
        request = RunnerRequest(
            system_prompt="Base prompt.",
            task="Do something.",
            cwd="/tmp",
        )
        request.append_system_prompt("APPENDED SECTION BETA")
        prompt_path = runner._write_system_prompt(request)
        try:
            with open(prompt_path) as f:
                content = f.read()
            assert "APPENDED SECTION BETA" in content
        finally:
            import os
            os.unlink(prompt_path)


# ---------------------------------------------------------------------------
# 7. Tool Control (allowed_tools)
# ---------------------------------------------------------------------------

class TestClaudeToolControl:
    def test_allowed_tools_in_command(self, runner):
        """allowed_tools=['Read', 'Grep'] -> --allowedTools Read,Grep in command."""
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            allowed_tools=["Read", "Grep"],
        )
        cmd = runner._build_command(request)
        assert "--allowedTools" in cmd
        idx = cmd.index("--allowedTools")
        assert cmd[idx + 1] == "Read,Grep"

    async def test_allowed_tools_real_call(self, runner, tmp_cwd):
        """Real call with restricted tools still completes."""
        request = RunnerRequest(
            system_prompt="Reply concisely.",
            task="Say OK.",
            cwd=tmp_cwd,
            timeout=60,
            max_turns=1,
            allowed_tools=["Read", "Grep"],
        )
        response = await runner.headless(request)
        assert isinstance(response, RunnerResponse)


# ---------------------------------------------------------------------------
# 8. Disallowed Tools
# ---------------------------------------------------------------------------

class TestClaudeDisallowedTools:
    def test_disallowed_tools_in_command(self, runner):
        """disallowed_tools=['Bash'] -> --disallowedTools Bash."""
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            disallowed_tools=["Bash"],
        )
        cmd = runner._build_command(request)
        assert "--disallowedTools" in cmd
        idx = cmd.index("--disallowedTools")
        assert cmd[idx + 1] == "Bash"


# ---------------------------------------------------------------------------
# 9. Permission Mode
# ---------------------------------------------------------------------------

class TestClaudePermissionMode:
    def test_auto_skip_permissions_adds_flag(self, runner):
        """PermissionMode.AUTO + skip_permissions=True -> --dangerously-skip-permissions."""
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            permission_mode=PermissionMode.AUTO,
            skip_permissions=True,
        )
        cmd = runner._build_command(request)
        assert "--dangerously-skip-permissions" in cmd

    def test_skip_permissions_false_no_flag(self, runner):
        """skip_permissions=False -> no --dangerously-skip-permissions flag."""
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            permission_mode=PermissionMode.AUTO,
            skip_permissions=False,
        )
        cmd = runner._build_command(request)
        assert "--dangerously-skip-permissions" not in cmd


# ---------------------------------------------------------------------------
# 10. Max Turns
# ---------------------------------------------------------------------------

class TestClaudeMaxTurns:
    def test_max_turns_in_command(self, runner):
        """max_turns=3 -> --max-turns 3 in command."""
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            max_turns=3,
        )
        cmd = runner._build_command(request)
        assert "--max-turns" in cmd
        idx = cmd.index("--max-turns")
        assert cmd[idx + 1] == "3"

    async def test_max_turns_real_call(self, runner, tmp_cwd):
        """Real call with max_turns=1 completes."""
        request = RunnerRequest(
            system_prompt="Reply concisely.",
            task="Say OK.",
            cwd=tmp_cwd,
            timeout=60,
            max_turns=1,
        )
        response = await runner.headless(request)
        assert response.exit_code == 0


# ---------------------------------------------------------------------------
# 11. Model Override
# ---------------------------------------------------------------------------

class TestClaudeModelOverride:
    def test_model_in_command(self, runner):
        """model='claude-sonnet-4-6' -> --model claude-sonnet-4-6 in command."""
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            model="claude-sonnet-4-6",
        )
        cmd = runner._build_command(request)
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# 12. Session Name
# ---------------------------------------------------------------------------

class TestClaudeSessionName:
    def test_session_name_in_command(self, runner):
        """session_name='test-session' -> --name test-session in command."""
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            session_name="test-session",
        )
        cmd = runner._build_command(request)
        assert "--name" in cmd
        idx = cmd.index("--name")
        assert cmd[idx + 1] == "test-session"

    async def test_session_id_returned(self, runner, tmp_cwd):
        """Real call returns session_id in response."""
        request = RunnerRequest(
            system_prompt="Reply concisely.",
            task="Say OK.",
            cwd=tmp_cwd,
            timeout=60,
            max_turns=1,
            session_name="test-cap-session",
        )
        response = await runner.headless(request)
        assert response.session_id is not None


# ---------------------------------------------------------------------------
# 13. Prompt Proxy (max_tokens, max_cost_usd)
# ---------------------------------------------------------------------------

class TestClaudePromptProxy:
    def test_max_tokens_not_in_command(self, runner):
        """max_tokens=100 -> NOT in command flags (no native support)."""
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            max_tokens=100,
        )
        cmd = runner._build_command(request)
        cmd_str = " ".join(cmd)
        assert "100" not in cmd_str or "--max-tokens" not in cmd_str

    def test_max_tokens_in_system_prompt_file(self, runner):
        """max_tokens=100 -> IS in the system prompt temp file content."""
        request = RunnerRequest(
            system_prompt="Base prompt.",
            task="Do something.",
            cwd="/tmp",
            max_tokens=100,
        )
        prompt_path = runner._write_system_prompt(request)
        try:
            with open(prompt_path) as f:
                content = f.read()
            assert "100 tokens" in content
        finally:
            import os
            os.unlink(prompt_path)

    def test_max_cost_usd_in_system_prompt_file(self, runner):
        """max_cost_usd=0.50 -> IS in the system prompt temp file content."""
        request = RunnerRequest(
            system_prompt="Base prompt.",
            task="Do something.",
            cwd="/tmp",
            max_cost_usd=0.50,
        )
        prompt_path = runner._write_system_prompt(request)
        try:
            with open(prompt_path) as f:
                content = f.read()
            assert "$0.50" in content
        finally:
            import os
            os.unlink(prompt_path)


# ---------------------------------------------------------------------------
# 14. V1/V2 Dispatch
# ---------------------------------------------------------------------------

class TestClaudeV1V2Dispatch:
    async def test_v2_dispatch_returns_runner_response(self, runner, tmp_cwd):
        """headless(RunnerRequest(...)) returns RunnerResponse."""
        request = RunnerRequest(
            system_prompt="Reply concisely.",
            task="Say OK.",
            cwd=tmp_cwd,
            timeout=60,
            max_turns=1,
        )
        response = await runner.headless(request)
        assert isinstance(response, RunnerResponse)

    async def test_v1_dispatch_returns_tuple(self, runner, tmp_cwd):
        """headless(prompt='...', task='...', cwd=...) returns tuple[str, int, AgentUsage|None]."""
        result = await runner.headless(
            prompt="Reply concisely.",
            task="Say OK.",
            cwd=tmp_cwd,
            timeout=60,
        )
        assert isinstance(result, tuple)
        assert len(result) == 3
        text, code, usage = result
        assert isinstance(text, str)
        assert isinstance(code, int)
