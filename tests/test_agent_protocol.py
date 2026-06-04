"""Tier 1 unit tests for the Agent protocol — pure tests for command building,
environment construction, and output parsing. No mocks needed."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from factory.runners.config import AgentLaunchConfig
from factory.runners.protocol import Agent, AgentResult
from factory.runners.claude import ClaudeCodeAgent
from factory.runners.codex import CodexAgent, CodexAuthError
from factory.runners.bob import BobShellAgent
from factory.runners.compositor import AgentRunner
from factory.runners.runtime import ProcessRuntime, TmuxRuntime
from factory.runners import get_runner, RunnerName, RUNNER_CHOICES


# ---------------------------------------------------------------------------
# AgentLaunchConfig
# ---------------------------------------------------------------------------

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
        assert config.mode == "headless"

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
            mode="interactive",
        )
        assert config.session_id == "abc123"
        assert config.role == "builder"
        assert config.model == "opus"
        assert config.permissions == "auto-edit"
        assert config.mode == "interactive"

    def test_rejects_extra_fields(self, tmp_path: Path) -> None:
        with pytest.raises(Exception):
            AgentLaunchConfig(
                project_path=tmp_path,
                prompt="p",
                task="t",
                unknown_field="x",  # type: ignore[call-arg]
            )

    def test_all_permission_modes(self, tmp_path: Path) -> None:
        for mode in ("permissionless", "auto-edit", "suggest"):
            config = AgentLaunchConfig(
                project_path=tmp_path, prompt="p", task="t", permissions=mode,
            )
            assert config.permissions == mode


# ---------------------------------------------------------------------------
# ClaudeCodeAgent
# ---------------------------------------------------------------------------

class TestClaudeCodeAgent:
    def _make_config(self, tmp_path: Path, **kwargs) -> AgentLaunchConfig:
        defaults: dict = dict(project_path=tmp_path, prompt="You are a builder", task="build it")
        defaults.update(kwargs)
        return AgentLaunchConfig(**defaults)

    # -- get_launch_command (headless) --

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

    # -- get_launch_command (interactive) --

    def test_get_launch_command_interactive_mode(self, tmp_path: Path) -> None:
        """Interactive mode: no -p, no --output-format json, task is positional."""
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, mode="interactive")
        cmd = agent.get_launch_command(config)

        assert cmd[0] == "claude"
        assert "--append-system-prompt-file" in cmd
        assert "-p" not in cmd
        assert "--output-format" not in cmd
        # Task is passed as positional arg after --dangerously-skip-permissions
        assert "--dangerously-skip-permissions" in cmd
        dsp_idx = cmd.index("--dangerously-skip-permissions")
        assert cmd[dsp_idx + 1] == "build it"
        agent.cleanup()

    def test_get_launch_command_interactive_suggest(self, tmp_path: Path) -> None:
        """Interactive + suggest: no skip permissions, task still positional."""
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, mode="interactive", permissions="suggest")
        cmd = agent.get_launch_command(config)

        assert "--dangerously-skip-permissions" not in cmd
        assert "build it" in cmd
        agent.cleanup()

    def test_get_launch_command_interactive_with_model_and_session(self, tmp_path: Path) -> None:
        """Interactive mode preserves --model and --name flags."""
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, mode="interactive", model="sonnet", session_name="s1")
        cmd = agent.get_launch_command(config)

        assert "--model" in cmd
        assert "sonnet" in cmd
        assert "--name" in cmd
        assert "s1" in cmd
        agent.cleanup()

    # -- get_environment --

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

    def test_get_environment_strips_virtual_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path)
        env = agent.get_environment(config)
        assert "VIRTUAL_ENV" not in env

    # -- parse_output --

    def test_parse_output_json(self) -> None:
        agent = ClaudeCodeAgent()
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
        assert result.usage.total_cost_usd == 0.01
        assert result.usage.duration_ms == 5000
        assert result.usage.num_turns == 3

    def test_parse_output_raw(self) -> None:
        agent = ClaudeCodeAgent()
        result = agent.parse_output("raw text output", 0)

        assert result.output == "raw text output"
        assert result.return_code == 0
        assert result.usage is None

    def test_parse_output_non_zero_exit(self) -> None:
        """parse_output preserves non-zero return codes."""
        agent = ClaudeCodeAgent()
        result = agent.parse_output("error output", 1)
        assert result.return_code == 1
        assert result.output == "error output"

    def test_parse_output_empty_json(self) -> None:
        """parse_output handles empty JSON dict gracefully."""
        agent = ClaudeCodeAgent()
        result = agent.parse_output("{}", 0)
        assert result.return_code == 0
        # Empty dict has no "result" key, falls back to raw stdout
        assert result.output == "{}"

    def test_parse_output_json_with_cache_tokens(self) -> None:
        """parse_output extracts cache read/creation tokens."""
        agent = ClaudeCodeAgent()
        data = {
            "result": "done",
            "usage": {
                "input_tokens": 200,
                "output_tokens": 100,
                "cache_read_input_tokens": 50,
                "cache_creation_input_tokens": 30,
            },
            "cost_usd": 0.05,
        }
        result = agent.parse_output(json.dumps(data), 0)
        assert result.usage is not None
        assert result.usage.cache_read_tokens == 50
        assert result.usage.cache_creation_tokens == 30

    # -- preflight --

    def test_preflight_succeeds(self) -> None:
        agent = ClaudeCodeAgent()
        agent.preflight()  # Should not raise

    # -- cleanup --

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

    def test_cleanup_multiple_invocations(self, tmp_path: Path) -> None:
        """cleanup removes all prompt files from multiple get_launch_command calls."""
        agent = ClaudeCodeAgent()
        config1 = self._make_config(tmp_path, task="task1")
        config2 = self._make_config(tmp_path, task="task2")
        agent.get_launch_command(config1)
        agent.get_launch_command(config2)
        assert len(agent._prompt_files) == 2
        files = list(agent._prompt_files)
        agent.cleanup()
        assert all(not f.exists() for f in files)

    def test_prompt_file_contains_prompt(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, prompt="You are a tester")
        agent.get_launch_command(config)
        prompt_file = agent._prompt_files[0]
        assert prompt_file.read_text() == "You are a tester"
        agent.cleanup()


# ---------------------------------------------------------------------------
# CodexAgent
# ---------------------------------------------------------------------------

class TestCodexAgent:
    def _make_config(self, tmp_path: Path, **kwargs) -> AgentLaunchConfig:
        defaults: dict = dict(project_path=tmp_path, prompt="You are a coder", task="code it")
        defaults.update(kwargs)
        return AgentLaunchConfig(**defaults)

    # -- get_launch_command --

    def test_get_launch_command_basic(self, tmp_path: Path) -> None:
        agent = CodexAgent()
        config = self._make_config(tmp_path)
        cmd = agent.get_launch_command(config)

        assert cmd[0] == "codex"
        assert cmd[1] == "exec"
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

    def test_get_launch_command_prompt_task_concatenation(self, tmp_path: Path) -> None:
        """Prompt and task are concatenated with separator in the codex exec argument."""
        agent = CodexAgent()
        config = self._make_config(tmp_path, prompt="SYSTEM", task="USER_TASK")
        cmd = agent.get_launch_command(config)
        full_prompt = cmd[2]
        assert "SYSTEM" in full_prompt
        assert "## Current Task" in full_prompt
        assert "USER_TASK" in full_prompt

    # -- get_environment --

    def test_get_environment_maps_codex_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        agent = CodexAgent()
        config = self._make_config(tmp_path)
        env = agent.get_environment(config)

        assert "VIRTUAL_ENV" not in env
        assert env.get("OPENAI_API_KEY") == "test-key"

    def test_get_environment_preserves_openai_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When OPENAI_API_KEY is already set, CODEX_API_KEY doesn't override it."""
        monkeypatch.setenv("CODEX_API_KEY", "codex-key")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        agent = CodexAgent()
        config = self._make_config(tmp_path)
        env = agent.get_environment(config)
        assert env.get("OPENAI_API_KEY") == "openai-key"

    # -- parse_output --

    def test_parse_output(self) -> None:
        agent = CodexAgent()
        result = agent.parse_output("some output", 0)

        assert result.output == "some output"
        assert result.return_code == 0
        assert result.usage is None

    def test_parse_output_non_zero(self) -> None:
        agent = CodexAgent()
        result = agent.parse_output("error", 2)
        assert result.return_code == 2
        assert result.usage is None

    # -- preflight --

    def test_preflight_dry_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_CODEX_DRY_RUN", "1")
        agent = CodexAgent()
        agent.preflight()  # Should not raise even without API key

    def test_preflight_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        # Reset the auth check cache
        import factory.runners.codex as codex_mod
        codex_mod._auth_checked = False
        agent = CodexAgent()
        agent.preflight()  # Should not raise

    def test_preflight_no_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        import factory.runners.codex as codex_mod
        codex_mod._auth_checked = False
        agent = CodexAgent()
        with pytest.raises(CodexAuthError):
            agent.preflight()


# ---------------------------------------------------------------------------
# BobShellAgent
# ---------------------------------------------------------------------------

class TestBobShellAgent:
    def _make_config(self, tmp_path: Path, **kwargs) -> AgentLaunchConfig:
        defaults: dict = dict(project_path=tmp_path, prompt="You are Bob", task="bob it")
        defaults.update(kwargs)
        return AgentLaunchConfig(**defaults)

    # -- get_launch_command --

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

    def test_get_launch_command_prompt_task_concatenation(self, tmp_path: Path) -> None:
        agent = BobShellAgent()
        config = self._make_config(tmp_path, prompt="SYS", task="TASK")
        cmd = agent.get_launch_command(config)
        idx = cmd.index("-p")
        full = cmd[idx + 1]
        assert "SYS" in full
        assert "## Current Task" in full
        assert "TASK" in full

    # -- get_environment --

    def test_get_environment(self, tmp_path: Path) -> None:
        agent = BobShellAgent()
        config = self._make_config(tmp_path)
        env = agent.get_environment(config)

        assert isinstance(env, dict)
        assert "PATH" in env

    # -- parse_output --

    def test_parse_output(self) -> None:
        agent = BobShellAgent()
        result = agent.parse_output("bob output", 0)

        assert result.output == "bob output"
        assert result.return_code == 0
        assert result.usage is None

    def test_parse_output_non_zero(self) -> None:
        agent = BobShellAgent()
        result = agent.parse_output("fail", 1)
        assert result.return_code == 1

    # -- preflight --

    def test_preflight_dry_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_BOB_DRY_RUN", "1")
        agent = BobShellAgent(project_path=tmp_path)
        agent.preflight()  # Should not raise

    def test_preflight_no_key_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BOBSHELL_API_KEY", raising=False)
        monkeypatch.delenv("FACTORY_BOB_DRY_RUN", raising=False)
        import factory.runners.bob as bob_mod
        bob_mod._auth_checked = False
        agent = BobShellAgent(project_path=tmp_path)
        from factory.runners.bob import BobAuthError
        with pytest.raises(BobAuthError):
            agent.preflight()


# ---------------------------------------------------------------------------
# AgentResult
# ---------------------------------------------------------------------------

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

    def test_non_zero_return_code(self) -> None:
        result = AgentResult(output="err", return_code=127)
        assert result.return_code == 127


# ---------------------------------------------------------------------------
# RunnerName / RUNNER_CHOICES
# ---------------------------------------------------------------------------

class TestRunnerName:
    def test_runner_choices_tuple(self) -> None:
        assert isinstance(RUNNER_CHOICES, tuple)
        assert "claude" in RUNNER_CHOICES
        assert "bob" in RUNNER_CHOICES
        assert "codex" in RUNNER_CHOICES

    def test_runner_choices_matches_literal(self) -> None:
        from typing import get_args
        assert RUNNER_CHOICES == get_args(RunnerName)


# ---------------------------------------------------------------------------
# get_runner() returns AgentRunner
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# AgentRunner compositor
# ---------------------------------------------------------------------------

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

    def test_from_legacy_name(self) -> None:
        """from_legacy preserves the legacy runner's name."""
        from factory.runners.bob import BobRunner
        legacy = BobRunner()
        wrapped = AgentRunner.from_legacy(legacy)
        assert wrapped.name == "bob"

    def test_from_legacy_getattr_proxy(self) -> None:
        """from_legacy proxies attribute access to the legacy runner."""
        from factory.runners.bob import BobRunner
        legacy = BobRunner()
        wrapped = AgentRunner.from_legacy(legacy)
        # BobRunner has cycle_start attribute
        assert hasattr(wrapped, "cycle_start")

    def test_getattr_raises_for_unknown(self) -> None:
        agent = ClaudeCodeAgent()
        runner = AgentRunner(agent)
        with pytest.raises(AttributeError):
            runner.nonexistent_attr  # noqa: B018

    def test_headless_builds_config_correctly(self) -> None:
        """headless() constructs AgentLaunchConfig with correct permission mapping."""
        agent = ClaudeCodeAgent()
        configs_seen: list[AgentLaunchConfig] = []

        original_get_cmd = agent.get_launch_command

        def spy_get_cmd(config: AgentLaunchConfig) -> list[str]:
            configs_seen.append(config)
            return ["echo", "test"]

        agent.get_launch_command = spy_get_cmd  # type: ignore[assignment]

        mock_runtime = MagicMock()
        mock_runtime.execute = MagicMock()

        import asyncio

        async def mock_execute(*a, **kw):  # type: ignore[no-untyped-def]
            return ("output", 0)

        mock_runtime.execute = mock_execute

        runner = AgentRunner(agent, mock_runtime)
        asyncio.run(runner.headless("prompt", "task", Path("/tmp"), role="builder"))

        assert len(configs_seen) == 1
        config = configs_seen[0]
        assert config.role == "builder"
        assert config.permissions == "permissionless"
        assert config.mode == "headless"

    def test_interactive_sets_mode(self) -> None:
        """interactive_run() sets mode='interactive' on the config."""
        agent = ClaudeCodeAgent()
        configs_seen: list[AgentLaunchConfig] = []

        def spy_get_cmd(config: AgentLaunchConfig) -> list[str]:
            configs_seen.append(config)
            return ["echo", "test"]

        agent.get_launch_command = spy_get_cmd  # type: ignore[assignment]

        mock_runtime = MagicMock()
        mock_runtime.execute_interactive = MagicMock(return_value=0)

        runner = AgentRunner(agent, mock_runtime)
        runner.interactive_run("prompt", "task", Path("/tmp"))

        assert len(configs_seen) == 1
        assert configs_seen[0].mode == "interactive"

    def test_headless_calls_cleanup(self) -> None:
        """headless() calls cleanup on agents that have it."""
        agent = ClaudeCodeAgent()
        mock_runtime = MagicMock()

        import asyncio

        async def mock_execute(*a, **kw):  # type: ignore[no-untyped-def]
            return ("output", 0)

        mock_runtime.execute = mock_execute
        runner = AgentRunner(agent, mock_runtime)

        asyncio.run(runner.headless("prompt", "task", Path("/tmp")))
        # After headless, prompt files should be cleaned up
        assert len(agent._prompt_files) == 0

    def test_interactive_calls_cleanup(self) -> None:
        """interactive_run() calls cleanup on agents that have it."""
        agent = ClaudeCodeAgent()
        mock_runtime = MagicMock()
        mock_runtime.execute_interactive = MagicMock(return_value=0)

        runner = AgentRunner(agent, mock_runtime)
        runner.interactive_run("prompt", "task", Path("/tmp"))
        assert len(agent._prompt_files) == 0

    def test_headless_suggest_permissions(self) -> None:
        """headless with dangerously_skip_permissions=False maps to 'suggest'."""
        agent = ClaudeCodeAgent()
        configs_seen: list[AgentLaunchConfig] = []

        def spy_get_cmd(config: AgentLaunchConfig) -> list[str]:
            configs_seen.append(config)
            return ["echo", "test"]

        agent.get_launch_command = spy_get_cmd  # type: ignore[assignment]

        mock_runtime = MagicMock()

        import asyncio

        async def mock_execute(*a, **kw):  # type: ignore[no-untyped-def]
            return ("output", 0)

        mock_runtime.execute = mock_execute

        runner = AgentRunner(agent, mock_runtime)
        asyncio.run(runner.headless(
            "prompt", "task", Path("/tmp"),
            dangerously_skip_permissions=False,
        ))

        assert configs_seen[0].permissions == "suggest"
