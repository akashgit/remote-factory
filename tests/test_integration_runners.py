"""Tier 2 integration tests for runners — real CLI invocations.

These tests require actual CLI tools installed and are skipped by default.
Run with: pytest -m integration tests/test_integration_runners.py -v

Coverage matrix (Claude + Codex):
- get_runner() composition
- headless() basic
- headless() with model override
- headless() with suggest permissions (non-skip)
- headless() output parsing (AgentResult fields)
- interactive_run() command construction (mocked subprocess)
- preflight() success path
- cleanup() after headless
- AgentRunner compositor end-to-end
"""

import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from factory.runners import get_runner
from factory.runners.claude import ClaudeCodeAgent
from factory.runners.codex import CodexAgent
from factory.runners.compositor import AgentRunner
from factory.runners.config import AgentLaunchConfig  # noqa: F401
from factory.runners.runtime import ProcessRuntime

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Claude integration (real CLI, real model)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude CLI not found on PATH",
)
class TestClaudeIntegration:
    async def test_headless_basic(self, tmp_path):
        """Full stack: get_runner → AgentRunner → headless → real claude CLI."""
        runner = get_runner("claude")
        assert isinstance(runner, AgentRunner)
        stdout, code, usage = await runner.headless(
            prompt="You are a test agent. Keep your response under 10 words.",
            task="Reply with exactly: HELLO_CLAUDE",
            cwd=tmp_path,
            timeout=60.0,
            role="test",
        )
        assert code == 0
        assert isinstance(stdout, str)
        assert len(stdout) > 0
        # Claude provides usage telemetry
        assert usage is not None
        assert usage.input_tokens > 0
        assert usage.output_tokens > 0

    async def test_headless_with_model(self, tmp_path):
        """Headless with explicit --model flag."""
        runner = get_runner("claude")
        stdout, code, usage = await runner.headless(
            prompt="You are a test agent. Keep your response under 10 words.",
            task="Reply with exactly: MODEL_TEST",
            cwd=tmp_path,
            timeout=60.0,
            model="haiku",
            role="test",
        )
        assert code == 0
        assert len(stdout) > 0
        assert usage is not None

    async def test_headless_output_parsing(self, tmp_path):
        """Verify parse_output correctly extracts result from Claude JSON."""
        runner = get_runner("claude")
        stdout, code, usage = await runner.headless(
            prompt="You are a test agent. Respond with exactly one word.",
            task="Say: PARSED",
            cwd=tmp_path,
            timeout=60.0,
            role="test",
        )
        assert code == 0
        # stdout should be the extracted "result" field, not raw JSON
        assert not stdout.startswith("{")
        assert usage is not None
        assert usage.input_tokens > 0
        assert usage.output_tokens > 0

    async def test_headless_cleanup(self, tmp_path):
        """Verify temp prompt files are cleaned up after headless invocation."""
        agent = ClaudeCodeAgent()
        runner = AgentRunner(agent, ProcessRuntime())
        await runner.headless(
            prompt="Test cleanup",
            task="Say hello",
            cwd=tmp_path,
            timeout=60.0,
            role="test",
        )
        # After headless, all temp files should be cleaned up
        assert len(agent._prompt_files) == 0

    async def test_headless_suggest_permissions(self, tmp_path):
        """Headless with permissions=suggest omits --dangerously-skip-permissions."""
        agent = ClaudeCodeAgent()
        config = AgentLaunchConfig(
            project_path=tmp_path,
            append_system_prompt="You are a test agent.",
            task="Reply with: SUGGEST_MODE",
            permissions="suggest",
        )
        cmd = agent.get_launch_command(config)
        assert "--dangerously-skip-permissions" not in cmd
        agent.cleanup()

    def test_interactive_command_construction(self, tmp_path):
        """interactive_run builds correct command (mocked subprocess)."""
        runner = get_runner("claude")
        with patch("factory.runners.runtime.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            code = runner.interactive_run(
                prompt="System prompt",
                task="Interactive task",
                cwd=tmp_path,
                dangerously_skip_permissions=True,
            )
        assert code == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "--append-system-prompt-file" in cmd
        assert "--dangerously-skip-permissions" in cmd
        # Interactive mode: task as positional, no -p, no --output-format
        assert "-p" not in cmd
        assert "--output-format" not in cmd
        assert "Interactive task" in cmd

    def test_preflight_success(self):
        """ClaudeCodeAgent.preflight() passes when claude is on PATH."""
        agent = ClaudeCodeAgent()
        agent.preflight()  # Should not raise


# ---------------------------------------------------------------------------
# Codex integration (real CLI, real model)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    shutil.which("codex") is None,
    reason="codex CLI not found on PATH",
)
@pytest.mark.skipif(
    not (os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY")),
    reason="CODEX_API_KEY or OPENAI_API_KEY not set",
)
class TestCodexIntegration:
    async def test_headless_basic(self, tmp_path):
        """Full stack: get_runner → AgentRunner → headless → real codex CLI."""
        runner = get_runner("codex")
        assert isinstance(runner, AgentRunner)
        stdout, code, usage = await runner.headless(
            prompt="You are a test agent. Keep your response under 10 words.",
            task="Reply with exactly: HELLO_CODEX",
            cwd=tmp_path,
            timeout=120.0,
            role="test",
        )
        assert code == 0
        assert isinstance(stdout, str)
        assert len(stdout) > 0
        # Codex has no usage telemetry
        assert usage is None

    async def test_headless_with_model(self, tmp_path):
        """Headless with explicit --model flag."""
        runner = get_runner("codex")
        stdout, code, usage = await runner.headless(
            prompt="You are a test agent.",
            task="Reply with exactly: MODEL_TEST",
            cwd=tmp_path,
            timeout=120.0,
            model="o4-mini",
            role="test",
        )
        assert code == 0
        assert len(stdout) > 0

    async def test_headless_output_is_raw(self, tmp_path):
        """Codex parse_output returns raw stdout (no JSON extraction)."""
        runner = get_runner("codex")
        stdout, code, usage = await runner.headless(
            prompt="You are a test agent.",
            task="Reply with exactly: RAW_OUTPUT",
            cwd=tmp_path,
            timeout=120.0,
            role="test",
        )
        assert code == 0
        assert isinstance(stdout, str)
        # No usage for codex
        assert usage is None

    async def test_headless_cleanup(self, tmp_path):
        """Codex agent has no prompt files to clean up (prompt is inline)."""
        agent = CodexAgent()
        runner = AgentRunner(agent, ProcessRuntime())
        await runner.headless(
            prompt="Test cleanup",
            task="Say hello",
            cwd=tmp_path,
            timeout=120.0,
            role="test",
        )
        # CodexAgent has no cleanup method — no temp files
        assert not hasattr(agent, "_prompt_files")

    async def test_headless_suggest_permissions(self, tmp_path):
        """Headless with permissions=suggest omits bypass flag."""
        agent = CodexAgent()
        config = AgentLaunchConfig(
            project_path=tmp_path,
            append_system_prompt="Test",
            task="Test",
            permissions="suggest",
        )
        cmd = agent.get_launch_command(config)
        assert "--dangerously-bypass-approvals-and-sandbox" not in cmd

    def test_interactive_command_construction(self, tmp_path):
        """interactive_run builds correct codex command (mocked subprocess)."""
        runner = get_runner("codex")
        with patch("factory.runners.runtime.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            code = runner.interactive_run(
                prompt="System prompt",
                task="Interactive task",
                cwd=tmp_path,
                dangerously_skip_permissions=True,
            )
        assert code == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "codex"
        # Codex always uses exec (interactive codex requires TTY)
        assert cmd[1] == "exec"
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd

    def test_preflight_success(self):
        """CodexAgent.preflight() passes when API key is set."""
        import factory.runners.codex as codex_mod
        codex_mod._auth_checked = False
        agent = CodexAgent()
        agent.preflight()  # Should not raise


# ---------------------------------------------------------------------------
# Cross-backend compositor tests (no real model needed)
# ---------------------------------------------------------------------------

class TestAgentRunnerCrossBackend:
    def test_claude_get_runner_returns_compositor(self):
        runner = get_runner("claude")
        assert isinstance(runner, AgentRunner)
        assert runner.name == "claude"

    def test_codex_get_runner_returns_compositor(self):
        runner = get_runner("codex")
        assert isinstance(runner, AgentRunner)
        assert runner.name == "codex"

    def test_compositor_uses_process_runtime_by_default(self):
        runner = get_runner("claude")
        assert isinstance(runner._runtime, ProcessRuntime)
