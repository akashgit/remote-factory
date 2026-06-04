"""Tier 2 integration tests for runners — real CLI invocations.

These tests require actual CLI tools installed and are skipped by default.
Run with: pytest -m integration tests/test_integration_runners.py -v
"""

import os
import shutil

import pytest

from factory.runners import get_runner
from factory.runners.compositor import AgentRunner

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude CLI not found on PATH",
)
class TestClaudeIntegration:
    async def test_claude_headless_echo(self, tmp_path):
        """Test that ClaudeCodeAgent can execute a simple headless task via AgentRunner."""
        runner = get_runner("claude")
        assert isinstance(runner, AgentRunner)
        stdout, code, usage = await runner.headless(
            prompt="You are a test agent. Respond with exactly: HELLO",
            task="Say HELLO",
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


@pytest.mark.skipif(
    shutil.which("codex") is None,
    reason="codex CLI not found on PATH",
)
@pytest.mark.skipif(
    not (os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY")),
    reason="CODEX_API_KEY or OPENAI_API_KEY not set",
)
class TestCodexIntegration:
    async def test_codex_headless_echo(self, tmp_path):
        """Test that CodexAgent can execute a simple headless task via AgentRunner."""
        runner = get_runner("codex")
        assert isinstance(runner, AgentRunner)
        stdout, code, usage = await runner.headless(
            prompt="You are a test agent. Respond with exactly: HELLO",
            task="Say HELLO",
            cwd=tmp_path,
            timeout=60.0,
            role="test",
        )
        assert code == 0
        assert isinstance(stdout, str)
        assert len(stdout) > 0
        # Codex has no usage telemetry
        assert usage is None


@pytest.mark.skipif(
    shutil.which("bob") is None,
    reason="bob CLI not found on PATH",
)
class TestBobIntegration:
    async def test_bob_dry_run(self, tmp_path, monkeypatch):
        """Test that BobShellAgent dry-run returns stub response via AgentRunner."""
        monkeypatch.setenv("FACTORY_BOB_DRY_RUN", "1")
        runner = get_runner("bob", project_path=tmp_path)
        assert isinstance(runner, AgentRunner)
        stdout, code, usage = await runner.headless(
            prompt="You are a test agent",
            task="Test task",
            cwd=tmp_path,
            timeout=60.0,
            role="test",
        )
        assert isinstance(stdout, str)
