"""Tier 1 unit tests for the Agent protocol — pure tests for command building,
environment construction, and output parsing. No mocks needed."""

from pathlib import Path

import pytest

from factory.runners.config import AgentLaunchConfig
from factory.runners.protocol import Agent, AgentResult
from factory.runners.claude import ClaudeCodeAgent
from factory.runners.codex import CodexAgent
from factory.runners.bob import BobShellAgent
from factory.runners.compositor import AgentRunner
from factory.runners.runtime import ProcessRuntime, TmuxRuntime
from factory.runners import get_runner, RunnerName, RUNNER_CHOICES


class TestAgentLaunchConfig:
    def test_default_values(self, tmp_path: Path) -> None:
        config = AgentLaunchConfig(
            project_path=tmp_path,
            prompt="system prompt",
            task="do something",
        )
        assert config.role == "unknown"
        assert config.model is None
        assert config.timeout == 600.0
        assert config.permissions == "permissionless"
        assert config.session_name is None
        assert config.session_id == ""

    def test_custom_values(self, tmp_path: Path) -> None:
        config = AgentLaunchConfig(
            session_id="abc123",
            project_path=tmp_path,
            prompt="prompt",
            task="task",
            role="builder",
            model="opus",
            timeout=300.0,
            permissions="auto-edit",
            session_name="my-session",
        )
        assert config.session_id == "abc123"
        assert config.role == "builder"
        assert config.model == "opus"
        assert config.permissions == "auto-edit"

    def test_rejects_extra_fields(self, tmp_path: Path) -> None:
        with pytest.raises(Exception):
            AgentLaunchConfig(
                project_path=tmp_path,
                prompt="p",
                task="t",
                unknown_field="x",  # type: ignore[call-arg]
            )


class TestClaudeCodeAgent:
    def _make_config(self, tmp_path: Path, **kwargs) -> AgentLaunchConfig:  # type: ignore[no-untyped-def]
        defaults = dict(project_path=tmp_path, prompt="You are a builder", task="build it")
        defaults.update(kwargs)
        return AgentLaunchConfig(**defaults)

    def test_get_launch_command_basic(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path)
        cmd = agent.get_launch_command(config)

        assert cmd[0] == "claude"
        assert "--append-system-prompt-file" in cmd
        assert "-p" in cmd
        idx = cmd.index("-p")
        assert cmd[idx + 1] == "build it"
        assert "--output-format" in cmd
        assert "json" in cmd
        assert "--dangerously-skip-permissions" in cmd
        agent.cleanup()

    def test_get_launch_command_with_model(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, model="opus")
        cmd = agent.get_launch_command(config)

        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "opus"
        agent.cleanup()

    def test_get_launch_command_suggest_permissions(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, permissions="suggest")
        cmd = agent.get_launch_command(config)

        assert "--dangerously-skip-permissions" not in cmd
        agent.cleanup()

    def test_get_launch_command_with_session_name(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, session_name="test-session")
        cmd = agent.get_launch_command(config)

        assert "--name" in cmd
        idx = cmd.index("--name")
        assert cmd[idx + 1] == "test-session"
        agent.cleanup()

    def test_get_environment(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, model="opus")
        env = agent.get_environment(config)

        assert "VIRTUAL_ENV" not in env
        assert env.get("FACTORY_MODEL") == "opus"

    def test_get_environment_no_model(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path)
        env = agent.get_environment(config)

        assert "FACTORY_MODEL" not in env

    def test_parse_output_json(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        import json
        data = {
            "result": "Build complete",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "cost_usd": 0.01,
            "duration_ms": 5000,
            "num_turns": 3,
            "model": "opus",
        }
        result = agent.parse_output(json.dumps(data), 0)

        assert isinstance(result, AgentResult)
        assert result.output == "Build complete"
        assert result.return_code == 0
        assert result.usage is not None
        assert result.usage.input_tokens == 100
        assert result.usage.output_tokens == 50

    def test_parse_output_raw(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        result = agent.parse_output("raw text output", 0)

        assert result.output == "raw text output"
        assert result.return_code == 0
        assert result.usage is None

    def test_preflight_succeeds(self) -> None:
        agent = ClaudeCodeAgent()
        agent.preflight()  # Should not raise

    def test_cleanup(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path)
        agent.get_launch_command(config)
        assert len(agent._prompt_files) == 1
        prompt_file = agent._prompt_files[0]
        assert prompt_file.exists()
        agent.cleanup()
        assert not prompt_file.exists()
        assert len(agent._prompt_files) == 0

    def test_prompt_file_contains_prompt(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, prompt="You are a tester")
        agent.get_launch_command(config)
        prompt_file = agent._prompt_files[0]
        assert prompt_file.read_text() == "You are a tester"
        agent.cleanup()


class TestCodexAgent:
    def _make_config(self, tmp_path: Path, **kwargs) -> AgentLaunchConfig:  # type: ignore[no-untyped-def]
        defaults = dict(project_path=tmp_path, prompt="You are a coder", task="code it")
        defaults.update(kwargs)
        return AgentLaunchConfig(**defaults)

    def test_get_launch_command_basic(self, tmp_path: Path) -> None:
        agent = CodexAgent()
        config = self._make_config(tmp_path)
        cmd = agent.get_launch_command(config)

        assert cmd[0] == "codex"
        assert cmd[1] == "exec"
        # The full prompt should contain both prompt and task
        full_prompt = cmd[2]
        assert "You are a coder" in full_prompt
        assert "code it" in full_prompt
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd

    def test_get_launch_command_with_model(self, tmp_path: Path) -> None:
        agent = CodexAgent()
        config = self._make_config(tmp_path, model="gpt-5")
        cmd = agent.get_launch_command(config)

        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "gpt-5"

    def test_get_launch_command_suggest_permissions(self, tmp_path: Path) -> None:
        agent = CodexAgent()
        config = self._make_config(tmp_path, permissions="suggest")
        cmd = agent.get_launch_command(config)

        assert "--dangerously-bypass-approvals-and-sandbox" not in cmd

    def test_get_environment(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        agent = CodexAgent()
        config = self._make_config(tmp_path)
        env = agent.get_environment(config)

        assert "VIRTUAL_ENV" not in env
        assert env.get("OPENAI_API_KEY") == "test-key"

    def test_parse_output(self) -> None:
        agent = CodexAgent()
        result = agent.parse_output("some output", 0)

        assert result.output == "some output"
        assert result.return_code == 0
        assert result.usage is None

    def test_preflight_dry_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_CODEX_DRY_RUN", "1")
        agent = CodexAgent()
        agent.preflight()  # Should not raise even without API key


class TestBobShellAgent:
    def _make_config(self, tmp_path: Path, **kwargs) -> AgentLaunchConfig:  # type: ignore[no-untyped-def]
        defaults = dict(project_path=tmp_path, prompt="You are Bob", task="bob it")
        defaults.update(kwargs)
        return AgentLaunchConfig(**defaults)

    def test_get_launch_command_basic(self, tmp_path: Path) -> None:
        agent = BobShellAgent()
        config = self._make_config(tmp_path)
        cmd = agent.get_launch_command(config)

        assert cmd[0] == "bob"
        assert "-p" in cmd
        idx = cmd.index("-p")
        full_task = cmd[idx + 1]
        assert "You are Bob" in full_task
        assert "bob it" in full_task
        assert "--chat-mode=code" in cmd
        assert "--yolo" in cmd

    def test_get_launch_command_suggest_permissions(self, tmp_path: Path) -> None:
        agent = BobShellAgent()
        config = self._make_config(tmp_path, permissions="suggest")
        cmd = agent.get_launch_command(config)

        assert "--yolo" not in cmd

    def test_get_environment(self, tmp_path: Path) -> None:
        agent = BobShellAgent()
        config = self._make_config(tmp_path)
        env = agent.get_environment(config)

        assert isinstance(env, dict)
        assert "PATH" in env

    def test_parse_output(self) -> None:
        agent = BobShellAgent()
        result = agent.parse_output("bob output", 0)

        assert result.output == "bob output"
        assert result.return_code == 0
        assert result.usage is None


class TestAgentResult:
    def test_basic(self) -> None:
        result = AgentResult(output="hello", return_code=0)
        assert result.output == "hello"
        assert result.return_code == 0
        assert result.usage is None

    def test_with_usage(self) -> None:
        from factory.models import AgentUsage
        usage = AgentUsage(input_tokens=10, output_tokens=5)
        result = AgentResult(output="done", return_code=0, usage=usage)
        assert result.usage is not None
        assert result.usage.input_tokens == 10


class TestRunnerName:
    def test_runner_choices_tuple(self) -> None:
        assert isinstance(RUNNER_CHOICES, tuple)
        assert "claude" in RUNNER_CHOICES
        assert "bob" in RUNNER_CHOICES
        assert "codex" in RUNNER_CHOICES

    def test_runner_choices_matches_literal(self) -> None:
        from typing import get_args
        assert RUNNER_CHOICES == get_args(RunnerName)


class TestGetRunnerReturnsAgentRunner:
    def test_default_is_claude(self) -> None:
        runner = get_runner()
        assert isinstance(runner, AgentRunner)
        assert runner.name == "claude"

    def test_explicit_claude(self) -> None:
        runner = get_runner("claude")
        assert isinstance(runner, AgentRunner)
        assert runner.name == "claude"

    def test_explicit_bob(self) -> None:
        runner = get_runner("bob")
        assert isinstance(runner, AgentRunner)
        assert runner.name == "bob"

    def test_explicit_codex(self) -> None:
        runner = get_runner("codex")
        assert isinstance(runner, AgentRunner)
        assert runner.name == "codex"

    def test_unknown_runner_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown runner 'unknown'"):
            get_runner("unknown")

    def test_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER", "bob")
        runner = get_runner()
        assert runner.name == "bob"

    def test_explicit_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER", "bob")
        runner = get_runner("claude")
        assert runner.name == "claude"


class TestAgentRunnerCompositor:
    def test_name_property(self) -> None:
        agent = ClaudeCodeAgent()
        runner = AgentRunner(agent)
        assert runner.name == "claude"

    def test_custom_runtime(self) -> None:
        agent = CodexAgent()
        runtime = ProcessRuntime()
        runner = AgentRunner(agent, runtime)
        assert runner.name == "codex"
