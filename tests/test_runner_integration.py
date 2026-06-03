"""Integration tests for the agent-runner abstraction.

Tests the full path: invoke_agent() -> get_runner() -> runner.run(Request) -> Response -> event emission.
Also tests runner registry, identity uniqueness, and edge cases.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.runners import _RUNNERS, get_runner
from factory.runners.abstraction import (
    AgentRunner,
    Request,
    Response,
)


# ── Runner registry tests ─────────────────────────────────────


class TestRunnerRegistry:
    def test_all_runners_resolve(self) -> None:
        """get_runner() resolves all registered runner names."""
        for name in _RUNNERS:
            if name == "bob":
                runner = get_runner(name, project_path=None)
            else:
                runner = get_runner(name)
            assert isinstance(runner, AgentRunner)

    def test_unique_identity_names(self) -> None:
        """All 5 runners have unique identity names."""
        names: set[str] = set()
        for key, cls in _RUNNERS.items():
            if key == "bob":
                runner = cls()
            else:
                runner = cls()
            name = runner.identity.name
            assert name not in names, f"Duplicate identity name: {name}"
            names.add(name)
        assert len(names) == 5

    def test_valid_binary_names(self) -> None:
        """All 5 runners have non-empty binary names."""
        for key, cls in _RUNNERS.items():
            runner = cls()
            assert runner.identity.binary, f"{key} has empty binary"
            assert isinstance(runner.identity.binary, str)

    def test_unknown_runner_raises(self) -> None:
        """Runner not found -> ValueError."""
        with pytest.raises(ValueError, match="Unknown runner"):
            get_runner("nonexistent")


# ── ClaudeRunner integration ──────────────────────────────────


class TestClaudeRunnerIntegration:
    async def test_invoke_agent_constructs_request(self, tmp_path: Path) -> None:
        """invoke_agent() -> get_runner() -> runner.run(Request) -> Response."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        mock_proc = AsyncMock()
        mock_proc.returncode = 0

        captured_request: list[Request] = []

        async def capture_run(self: AgentRunner, request: Request) -> Response:
            captured_request.append(request)
            return Response(output="test output", exit_code=0)

        with patch("factory.runners.abstraction.AgentRunner.run", capture_run), \
             patch("factory.agents.runner.get_runner") as mock_get_runner, \
             patch("factory.agents.runner.resolve_prompt", return_value="You are a builder."):
            from factory.runners.claude import ClaudeRunner
            runner = ClaudeRunner()
            mock_get_runner.return_value = runner

            from factory.agents.runner import invoke_agent
            stdout, return_code = await invoke_agent(
                "builder", "Build feature X", project,
                timeout=120.0, model="claude-sonnet-4-6",
                _track_failures=False,
            )

        assert return_code == 0
        assert stdout == "test output"
        assert len(captured_request) == 1
        req = captured_request[0]
        assert req.system_prompt == "You are a builder."
        assert req.task == "Build feature X"
        assert req.timeout == 120.0
        assert req.model == "claude-sonnet-4-6"
        assert req.role == "builder"

    async def test_claude_request_constructed_correctly(self, tmp_path: Path) -> None:
        """ClaudeRunner._build_command() maps Request fields to CLI args."""
        from factory.runners.claude import ClaudeRunner

        runner = ClaudeRunner()
        req = Request(
            system_prompt="sys", task="do stuff", cwd=str(tmp_path),
            model="claude-sonnet-4-6", session_name="sess1",
        )
        cmd = runner._build_command(req, prompt_file="/tmp/prompt.md")
        assert cmd[0] == "claude"
        assert "--append-system-prompt-file" in cmd
        assert "/tmp/prompt.md" in cmd
        assert "-p" in cmd
        assert "do stuff" in cmd
        assert "--model" in cmd
        assert "claude-sonnet-4-6" in cmd
        assert "--name" in cmd
        assert "sess1" in cmd
        assert "--output-format" in cmd
        assert "json" in cmd

    async def test_claude_response_unpacked(self, tmp_path: Path) -> None:
        """Response from ClaudeRunner is unpacked to (stdout, return_code) by invoke_agent."""
        import json

        from factory.runners.claude import ClaudeRunner

        runner = ClaudeRunner()
        raw_json = json.dumps({"result": "review complete", "cost_usd": 0.01})
        resp = runner._parse_response(raw_json, "", 0)
        assert resp.output == "review complete"
        assert resp.exit_code == 0
        assert resp.usage is not None
        assert resp.usage.total_cost_usd == 0.01


# ── CodexRunner integration ──────────────────────────────────


class TestCodexRunnerIntegration:
    def test_codex_env_maps_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CodexRunner._build_env() maps CODEX_API_KEY -> OPENAI_API_KEY."""
        monkeypatch.setenv("CODEX_API_KEY", "sk-test-123")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        from factory.runners.codex import CodexRunner
        runner = CodexRunner()
        req = Request(system_prompt="sp", task="t", cwd="/tmp")
        env = runner._build_env(req)
        assert env["OPENAI_API_KEY"] == "sk-test-123"

    def test_codex_command_uses_prompt_property(self) -> None:
        """CodexRunner._build_command() uses request.prompt (combined)."""
        from factory.runners.codex import CodexRunner

        runner = CodexRunner()
        req = Request(system_prompt="You are a reviewer.", task="Review PR", cwd="/tmp")
        cmd = runner._build_command(req)
        assert cmd[0] == "codex"
        assert cmd[1] == "exec"
        # The prompt should contain both system_prompt and task
        assert "You are a reviewer." in cmd[2]
        assert "Review PR" in cmd[2]


# ── OpenCodeRunner integration ────────────────────────────────


class TestOpenCodeRunnerIntegration:
    def test_model_with_provider_prefix(self) -> None:
        """OpenCodeRunner passes through model with provider/ prefix."""
        from factory.runners.opencode import OpenCodeRunner

        runner = OpenCodeRunner()
        req = Request(
            system_prompt="sp", task="t", cwd="/tmp",
            model="openai/gpt-4o",
        )
        cmd = runner._build_command(req)
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "openai/gpt-4o"

    def test_model_without_provider_gets_anthropic_prefix(self) -> None:
        """OpenCodeRunner adds anthropic/ prefix when model has no /."""
        from factory.runners.opencode import OpenCodeRunner

        runner = OpenCodeRunner()
        req = Request(
            system_prompt="sp", task="t", cwd="/tmp",
            model="claude-sonnet-4-6",
        )
        cmd = runner._build_command(req)
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "anthropic/claude-sonnet-4-6"


# ── AiderRunner integration ──────────────────────────────────


class TestAiderRunnerIntegration:
    def test_minimal_command(self) -> None:
        """AiderRunner builds minimal command with --message and --yes."""
        from factory.runners.aider import AiderRunner

        runner = AiderRunner()
        req = Request(system_prompt="sp", task="t", cwd="/tmp")
        cmd = runner._build_command(req)
        assert cmd[0] == "aider"
        assert "--message" in cmd
        assert "--yes" in cmd


# ── invoke_agent event emission ───────────────────────────────


class TestInvokeAgentEvents:
    async def test_events_emitted_on_success(self, tmp_path: Path) -> None:
        """invoke_agent() emits agent.started and agent.completed events."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        emitted: list[str] = []

        def track_emit(path: Path, event_type: str, **kwargs: object) -> None:
            emitted.append(event_type)

        async def mock_run(self: AgentRunner, request: Request) -> Response:
            return Response(output="done", exit_code=0)

        with patch("factory.runners.abstraction.AgentRunner.run", mock_run), \
             patch("factory.agents.runner.get_runner") as mock_get_runner, \
             patch("factory.agents.runner.resolve_prompt", return_value="prompt"), \
             patch("factory.agents.runner._emit_safe", side_effect=track_emit):
            from factory.runners.claude import ClaudeRunner
            mock_get_runner.return_value = ClaudeRunner()

            from factory.agents.runner import invoke_agent
            await invoke_agent("researcher", "research X", project, _track_failures=False)

        assert "agent.started" in emitted
        assert "agent.completed" in emitted

    async def test_events_emitted_on_failure(self, tmp_path: Path) -> None:
        """invoke_agent() emits agent.started and agent.failed on non-zero exit."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        emitted: list[str] = []

        def track_emit(path: Path, event_type: str, **kwargs: object) -> None:
            emitted.append(event_type)

        async def mock_run(self: AgentRunner, request: Request) -> Response:
            return Response(output="error", exit_code=1)

        with patch("factory.runners.abstraction.AgentRunner.run", mock_run), \
             patch("factory.agents.runner.get_runner") as mock_get_runner, \
             patch("factory.agents.runner.resolve_prompt", return_value="prompt"), \
             patch("factory.agents.runner._emit_safe", side_effect=track_emit):
            from factory.runners.claude import ClaudeRunner
            mock_get_runner.return_value = ClaudeRunner()

            from factory.agents.runner import invoke_agent
            await invoke_agent("builder", "build X", project, _track_failures=False)

        assert "agent.started" in emitted
        assert "agent.failed" in emitted


# ── Edge cases ────────────────────────────────────────────────


class TestEdgeCases:
    async def test_timeout_returns_error_response(self, tmp_path: Path) -> None:
        """Timeout -> process killed, error Response returned."""
        from factory.runners.claude import ClaudeRunner

        runner = ClaudeRunner()
        req = Request(system_prompt="sp", task="t", cwd=str(tmp_path), timeout=0.001)

        mock_proc = AsyncMock()
        mock_proc.returncode = -9

        with patch("factory.runners.abstraction.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("factory.runners.abstraction.stream_subprocess", side_effect=asyncio.TimeoutError), \
             patch("factory.runners.abstraction.should_stream", return_value=False):
            resp = await runner.run(req)

        assert resp.exit_code == 1
        assert "timed out" in resp.output.lower()
        assert resp.error is not None
        mock_proc.kill.assert_called_once()

    async def test_binary_not_found_handled(self, tmp_path: Path) -> None:
        """Binary not found -> FileNotFoundError handled gracefully."""
        from factory.runners.claude import ClaudeRunner

        runner = ClaudeRunner()
        req = Request(system_prompt="sp", task="t", cwd=str(tmp_path))

        with patch("factory.runners.abstraction.asyncio.create_subprocess_exec", side_effect=FileNotFoundError), \
             patch("factory.runners.abstraction.should_stream", return_value=False):
            resp = await runner.run(req)

        assert resp.exit_code == 1
        assert "not found" in resp.output.lower()
        assert resp.error is not None

    async def test_review_saved_on_success(self, tmp_path: Path) -> None:
        """Agent output is saved to .factory/reviews/<role>-latest.md."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        async def mock_run(self: AgentRunner, request: Request) -> Response:
            return Response(output="analysis complete", exit_code=0)

        with patch("factory.runners.abstraction.AgentRunner.run", mock_run), \
             patch("factory.agents.runner.get_runner") as mock_get_runner, \
             patch("factory.agents.runner.resolve_prompt", return_value="prompt"):
            from factory.runners.claude import ClaudeRunner
            mock_get_runner.return_value = ClaudeRunner()

            from factory.agents.runner import invoke_agent
            await invoke_agent("researcher", "analyze", project, _track_failures=False)

        review_file = project / ".factory" / "reviews" / "researcher-latest.md"
        assert review_file.exists()
        content = review_file.read_text()
        assert "analysis complete" in content
        assert "Researcher" in content


# ── _classify_with_llm integration ────────────────────────────


class TestClassifyWithLlm:
    def test_uses_request_response_interface(self) -> None:
        """_classify_with_llm() uses Request/Response when runner is AgentRunner."""
        import json

        from factory.runners.claude import ClaudeRunner

        response_json = json.dumps({
            "follow_ups": [],
            "suggestions": [{"label": "test", "command": "factory ceo /tmp"}],
        })
        mock_response = Response(output=response_json, exit_code=0)

        async def mock_run(self: AgentRunner, request: Request) -> Response:
            assert request.role == "wizard"
            assert request.timeout == 60.0
            assert request.skip_permissions is True
            return mock_response

        with patch("factory.runners.abstraction.AgentRunner.run", mock_run), \
             patch("factory.runners.get_runner", return_value=ClaudeRunner()), \
             patch("factory.cli._show_spinner"):
            from factory.cli import _classify_with_llm
            result = _classify_with_llm("build a weather app")

        assert result is not None
        follow_ups, suggestions = result
        assert len(suggestions) == 1
        assert suggestions[0]["label"] == "test"
