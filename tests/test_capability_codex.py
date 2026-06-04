"""Per-capability tests for CodexRunner using REAL Codex CLI.

Each test exercises ONE capability through the v2 CLIAdapter.headless() path.
Uses simple tasks to minimize token usage.

Run: uv run pytest tests/test_capability_codex.py -v -m e2e --timeout=120
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from factory.runners.codex import CodexRunner
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
    """Provide a temp directory initialized as a git repo (codex requires it)."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path, capture_output=True,
    )
    return str(tmp_path)


# ---------------------------------------------------------------------------
# 1. Health Check
# ---------------------------------------------------------------------------

class TestCodexHealthCheck:
    async def test_check_health(self, runner):
        healthy, msg = await runner.check_health()
        assert healthy is True
        assert "codex found" in msg


# ---------------------------------------------------------------------------
# 2. Basic Headless
# ---------------------------------------------------------------------------

class TestCodexBasicHeadless:
    async def test_v2_request_returns_runner_response(self, runner, tmp_cwd):
        """v2 RunnerRequest -> RunnerResponse. exit_code=0, output non-empty."""
        request = RunnerRequest(
            system_prompt="You are a helpful assistant. Reply concisely.",
            task="Reply with exactly: HELLO",
            cwd=tmp_cwd,
            timeout=60,
        )
        response = await runner.headless(request)
        assert isinstance(response, RunnerResponse)
        assert response.exit_code == 0
        assert len(response.output) > 0


# ---------------------------------------------------------------------------
# 3. Usage Stats
# ---------------------------------------------------------------------------

class TestCodexUsageStats:
    async def test_usage_parsed_from_jsonl(self, runner, tmp_cwd):
        """usage parsed from JSONL output."""
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


# ---------------------------------------------------------------------------
# 4. Trace
# ---------------------------------------------------------------------------

class TestCodexTrace:
    async def test_trace_has_steps(self, runner, tmp_cwd):
        """trace has >= 1 step."""
        request = RunnerRequest(
            system_prompt="Reply concisely.",
            task="Say OK.",
            cwd=tmp_cwd,
            timeout=60,
        )
        response = await runner.headless(request)
        assert response.trace is not None
        assert len(response.trace.steps) >= 1


# ---------------------------------------------------------------------------
# 5. System Prompt (inline via .prompt)
# ---------------------------------------------------------------------------

class TestCodexSystemPrompt:
    async def test_secret_word_via_system_prompt(self, runner, tmp_cwd):
        """System prompt inlined via request.prompt property. Secret word test."""
        request = RunnerRequest(
            system_prompt="The secret word is BANANA. When asked for the secret word, reply with BANANA.",
            task="What is the secret word?",
            cwd=tmp_cwd,
            timeout=60,
        )
        response = await runner.headless(request)
        assert "BANANA" in response.output


# ---------------------------------------------------------------------------
# 6. Sandbox Modes
# ---------------------------------------------------------------------------

class TestCodexSandboxModes:
    def test_read_only(self, runner):
        """READ_ONLY -> --sandbox read-only."""
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            sandbox_mode=SandboxMode.READ_ONLY,
        )
        cmd = runner._build_command(request)
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "read-only"

    def test_workspace_write(self, runner):
        """WORKSPACE_WRITE -> --sandbox workspace-write."""
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            sandbox_mode=SandboxMode.WORKSPACE_WRITE,
        )
        cmd = runner._build_command(request)
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "workspace-write"

    def test_full(self, runner):
        """FULL -> --sandbox danger-full-access."""
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            sandbox_mode=SandboxMode.FULL,
        )
        cmd = runner._build_command(request)
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "danger-full-access"

    def test_none_bypasses(self, runner):
        """NONE -> --dangerously-bypass-approvals-and-sandbox (no --sandbox flag)."""
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            sandbox_mode=SandboxMode.NONE,
        )
        cmd = runner._build_command(request)
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "--sandbox" not in cmd


# ---------------------------------------------------------------------------
# 7. Model Override
# ---------------------------------------------------------------------------

class TestCodexModelOverride:
    def test_model_in_command(self, runner):
        """model='o3' -> --model o3."""
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
            model="o3",
        )
        cmd = runner._build_command(request)
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "o3"


# ---------------------------------------------------------------------------
# 8. JSON Flag
# ---------------------------------------------------------------------------

class TestCodexJsonFlag:
    def test_json_always_present(self, runner):
        """--json is always present in command."""
        request = RunnerRequest(
            system_prompt="s", task="t", cwd="/tmp",
        )
        cmd = runner._build_command(request)
        assert "--json" in cmd


# ---------------------------------------------------------------------------
# 9. Prompt Proxy (allowed_tools, max_turns, max_tokens)
# ---------------------------------------------------------------------------

class TestCodexPromptProxy:
    def test_allowed_tools_in_system_prompt(self, runner):
        """allowed_tools -> in system prompt text (not CLI flags)."""
        request = RunnerRequest(
            system_prompt="Base prompt.",
            task="Do something.",
            cwd="/tmp",
            allowed_tools=["Read", "Grep"],
        )
        # Verify NOT in CLI command
        cmd = runner._build_command(request)
        assert "--allowedTools" not in cmd

        # Verify IS in system prompt temp file
        prompt_path = runner._write_system_prompt(request)
        try:
            with open(prompt_path) as f:
                content = f.read()
            assert "Read" in content
            assert "Grep" in content
        finally:
            import os
            os.unlink(prompt_path)

    def test_max_turns_in_system_prompt(self, runner):
        """max_turns -> in system prompt text (not CLI flags)."""
        request = RunnerRequest(
            system_prompt="Base prompt.",
            task="Do something.",
            cwd="/tmp",
            max_turns=5,
        )
        cmd = runner._build_command(request)
        assert "--max-turns" not in cmd

        prompt_path = runner._write_system_prompt(request)
        try:
            with open(prompt_path) as f:
                content = f.read()
            assert "5 conversation turns" in content
        finally:
            import os
            os.unlink(prompt_path)

    def test_max_tokens_in_system_prompt(self, runner):
        """max_tokens -> in system prompt text (not CLI flags)."""
        request = RunnerRequest(
            system_prompt="Base prompt.",
            task="Do something.",
            cwd="/tmp",
            max_tokens=200,
        )
        prompt_path = runner._write_system_prompt(request)
        try:
            with open(prompt_path) as f:
                content = f.read()
            assert "200 tokens" in content
        finally:
            import os
            os.unlink(prompt_path)


# ---------------------------------------------------------------------------
# 10. V1/V2 Dispatch
# ---------------------------------------------------------------------------

class TestCodexV1V2Dispatch:
    async def test_v2_dispatch_returns_runner_response(self, runner, tmp_cwd):
        """headless(RunnerRequest(...)) returns RunnerResponse."""
        request = RunnerRequest(
            system_prompt="Reply concisely.",
            task="Say OK.",
            cwd=tmp_cwd,
            timeout=60,
        )
        response = await runner.headless(request)
        assert isinstance(response, RunnerResponse)

    async def test_v1_dispatch_returns_tuple(self, runner, tmp_cwd):
        """headless(prompt='...', task='...', cwd=...) returns tuple[str, int, None]."""
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
        # Codex v1 always returns None for usage
        assert usage is None
