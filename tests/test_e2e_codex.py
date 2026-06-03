"""End-to-end tests for CodexRunner with real Codex CLI.

These tests spawn actual `codex` subprocesses and cost tokens.
Run explicitly with: uv run pytest tests/test_e2e_codex.py -v -m e2e

Verified against codex-cli 0.136.0.

Prerequisites:
- Codex CLI installed (`npm install -g @openai/codex` or cargo install)
- Authenticated via `codex login` or OPENAI_API_KEY env var
"""

from __future__ import annotations

import shutil

import pytest

from factory.runners.codex import CodexRunner, _parse_codex_jsonl
from factory.runners.types import (
    RunnerRequest,
    RunnerResponse,
    SandboxMode,
)

pytestmark = pytest.mark.e2e

# Skip entire module if codex CLI not available
if not shutil.which("codex"):
    pytest.skip("codex CLI not found", allow_module_level=True)


@pytest.fixture
def runner():
    return CodexRunner()


@pytest.fixture
def tmp_cwd(tmp_path):
    """Provide a temp directory as working directory for agent invocations."""
    # Initialize a git repo so codex doesn't complain
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=tmp_path, capture_output=True)
    return str(tmp_path)


# -- Health Check -------------------------------------------------------------

class TestCodexHealthCheck:
    async def test_check_health(self, runner):
        healthy, msg = await runner.check_health()
        assert healthy is True
        assert "codex found" in msg


# -- JSONL Parser (unit tests, no subprocess) ---------------------------------

class TestCodexJsonlParser:
    def test_parse_simple_response(self):
        jsonl = (
            '{"type":"thread.started","thread_id":"abc-123"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"HELLO"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":1000,"cached_input_tokens":800,'
            '"output_tokens":5,"reasoning_output_tokens":0}}\n'
        )
        text, usage, trace = _parse_codex_jsonl(jsonl)
        assert text == "HELLO"
        assert usage is not None
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 5
        assert usage.total_tokens == 1005
        assert trace is not None
        assert len(trace.steps) == 1
        assert trace.steps[0].output_text == "HELLO"

    def test_parse_empty_output(self):
        text, usage, trace = _parse_codex_jsonl("")
        assert text == ""
        assert usage is None
        assert trace is None

    def test_parse_malformed_lines_skipped(self):
        jsonl = (
            'not json\n'
            '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"OK"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":500,"output_tokens":3}}\n'
        )
        text, usage, trace = _parse_codex_jsonl(jsonl)
        assert text == "OK"
        assert usage is not None


# -- Basic Headless -----------------------------------------------------------

class TestCodexBasicHeadless:
    async def test_v2_request_returns_response(self, runner, tmp_cwd):
        """v2 RunnerRequest → RunnerResponse with parsed JSONL output."""
        request = RunnerRequest(
            system_prompt="You are a helpful assistant. Reply concisely.",
            task="Reply with exactly: HELLO",
            cwd=tmp_cwd,
            timeout=60,
        )
        response = await runner.headless(request)
        assert isinstance(response, RunnerResponse)
        assert response.exit_code == 0
        assert "HELLO" in response.output

    async def test_usage_stats_from_jsonl(self, runner, tmp_cwd):
        """Usage stats should be parsed from --json JSONL output."""
        request = RunnerRequest(
            system_prompt="Reply concisely.",
            task="What is 2+2? Reply with just the number.",
            cwd=tmp_cwd,
            timeout=60,
        )
        response = await runner.headless(request)
        assert response.usage is not None
        assert response.usage.input_tokens is not None
        assert response.usage.input_tokens > 0
        assert response.usage.output_tokens is not None
        assert response.usage.output_tokens > 0

    async def test_trace_populated(self, runner, tmp_cwd):
        """Execution trace should have at least one step from JSONL parsing."""
        request = RunnerRequest(
            system_prompt="Reply concisely.",
            task="Say OK.",
            cwd=tmp_cwd,
            timeout=60,
        )
        response = await runner.headless(request)
        assert response.trace is not None
        assert len(response.trace.steps) >= 1


# -- System Prompt (inline) ---------------------------------------------------

class TestCodexSystemPrompt:
    async def test_system_prompt_in_prompt(self, runner, tmp_cwd):
        """System prompt is inlined via .prompt property for Codex."""
        request = RunnerRequest(
            system_prompt="You are a calculator. Only output numbers, nothing else.",
            task="What is 7 * 8?",
            cwd=tmp_cwd,
            timeout=60,
        )
        response = await runner.headless(request)
        assert "56" in response.output

    async def test_append_system_prompt(self, runner, tmp_cwd):
        """Appended system prompt sections should be included in the inline prompt."""
        request = RunnerRequest(
            system_prompt="You are a helpful assistant.",
            task="What is the secret word?",
            cwd=tmp_cwd,
            timeout=60,
        )
        request.append_system_prompt("IMPORTANT: The secret word is BANANA. When asked, reply with it.")
        response = await runner.headless(request)
        assert "BANANA" in response.output


# -- Sandbox Modes ------------------------------------------------------------

class TestCodexSandbox:
    def test_sandbox_read_only_command(self, runner):
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            sandbox_mode=SandboxMode.READ_ONLY,
        )
        cmd = runner._build_command(request)
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "read-only"

    def test_sandbox_workspace_write_command(self, runner):
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            sandbox_mode=SandboxMode.WORKSPACE_WRITE,
        )
        cmd = runner._build_command(request)
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "workspace-write"

    def test_sandbox_full_command(self, runner):
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            sandbox_mode=SandboxMode.FULL,
        )
        cmd = runner._build_command(request)
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "danger-full-access"

    def test_sandbox_none_bypasses(self, runner):
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            sandbox_mode=SandboxMode.NONE,
        )
        cmd = runner._build_command(request)
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "--sandbox" not in cmd


# -- Model Override -----------------------------------------------------------

class TestCodexModel:
    def test_model_in_command(self, runner):
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            model="o3",
        )
        cmd = runner._build_command(request)
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "o3"
