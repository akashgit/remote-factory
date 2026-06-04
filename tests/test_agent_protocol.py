"""Tier 1 unit tests for the Agent protocol — pure tests for command building,
environment construction, and output parsing. No mocks needed.

Coverage matrix per semantic config field:

    | Field                | Claude | Codex |
    |----------------------|--------|-------|
    | system_prompt        | ✓      | ✓     |
    | append_system_prompt | ✓      | ✓     |
    | task                 | ✓      | ✓     |
    | allowed_tools        | ✓      | N/A   |
    | disallowed_tools     | ✓      | N/A   |
    | model                | ✓      | ✓     |
    | permissions          | ✓      | ✓     |
    | session_name         | ✓      | N/A   |
    | max_budget_usd       | ✓      | N/A   |
    | add_dirs             | ✓      | ✓     |
    | mode (headless)      | ✓      | ✓     |
    | mode (interactive)   | ✓      | ✓     |
    | parse_output         | ✓      | ✓     |
    | preflight            | ✓      | ✓     |
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from factory.runners.config import AgentLaunchConfig
from factory.runners.protocol import AgentResult
from factory.runners.claude import ClaudeCodeAgent
from factory.runners.codex import CodexAgent, CodexAuthError
from factory.runners.bob import BobShellAgent
from factory.runners.compositor import AgentRunner
from factory.runners.runtime import ProcessRuntime
from factory.runners import get_runner, RunnerName, RUNNER_CHOICES


# ---------------------------------------------------------------------------
# AgentLaunchConfig
# ---------------------------------------------------------------------------

class TestAgentLaunchConfig:
    def test_default_values(self, tmp_path: Path) -> None:
        config = AgentLaunchConfig(project_path=tmp_path, task="do something")
        assert config.system_prompt is None
        assert config.append_system_prompt is None
        assert config.allowed_tools is None
        assert config.disallowed_tools is None
        assert config.model is None
        assert config.timeout == 600.0
        assert config.permissions == "permissionless"
        assert config.max_budget_usd is None
        assert config.session_name is None
        assert config.session_id == ""
        assert config.mode == "headless"
        assert config.add_dirs is None

    def test_all_fields(self, tmp_path: Path) -> None:
        config = AgentLaunchConfig(
            project_path=tmp_path,
            system_prompt="You are X",
            append_system_prompt="Also do Y",
            task="build it",
            allowed_tools=["Bash", "Edit"],
            disallowed_tools=["WebSearch"],
            model="opus",
            timeout=300.0,
            permissions="suggest",
            max_budget_usd=1.50,
            session_id="abc",
            session_name="my-session",
            role="builder",
            mode="interactive",
            add_dirs=[Path("/extra")],
        )
        assert config.system_prompt == "You are X"
        assert config.append_system_prompt == "Also do Y"
        assert config.allowed_tools == ["Bash", "Edit"]
        assert config.disallowed_tools == ["WebSearch"]
        assert config.max_budget_usd == 1.50
        assert config.add_dirs == [Path("/extra")]
        assert config.mode == "interactive"

    def test_rejects_extra_fields(self, tmp_path: Path) -> None:
        with pytest.raises(Exception):
            AgentLaunchConfig(project_path=tmp_path, task="t", unknown="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ClaudeCodeAgent — semantic field mapping
# ---------------------------------------------------------------------------

class TestClaudeCodeAgent:
    def _make_config(self, tmp_path: Path, **kwargs) -> AgentLaunchConfig:
        defaults: dict = dict(project_path=tmp_path, append_system_prompt="You are a builder", task="build it")
        defaults.update(kwargs)
        return AgentLaunchConfig(**defaults)

    # -- system_prompt → --system-prompt --

    def test_system_prompt(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, system_prompt="You are CEO", append_system_prompt=None)
        cmd = agent.get_launch_command(config)
        assert "--system-prompt" in cmd
        idx = cmd.index("--system-prompt")
        assert cmd[idx + 1] == "You are CEO"
        agent.cleanup()

    # -- append_system_prompt → --append-system-prompt-file --

    def test_append_system_prompt(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, append_system_prompt="Extra instructions")
        cmd = agent.get_launch_command(config)
        assert "--append-system-prompt-file" in cmd
        idx = cmd.index("--append-system-prompt-file")
        prompt_path = Path(cmd[idx + 1])
        assert prompt_path.exists()
        assert prompt_path.read_text() == "Extra instructions"
        agent.cleanup()

    def test_both_system_and_append(self, tmp_path: Path) -> None:
        """Both --system-prompt and --append-system-prompt-file can coexist."""
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, system_prompt="Base", append_system_prompt="Extra")
        cmd = agent.get_launch_command(config)
        assert "--system-prompt" in cmd
        assert "--append-system-prompt-file" in cmd
        agent.cleanup()

    def test_no_prompt_flags_when_none(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, system_prompt=None, append_system_prompt=None)
        cmd = agent.get_launch_command(config)
        assert "--system-prompt" not in cmd
        assert "--append-system-prompt-file" not in cmd
        agent.cleanup()

    # -- allowed_tools → --allowedTools --

    def test_allowed_tools(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, allowed_tools=["Bash", "Edit", "Read"])
        cmd = agent.get_launch_command(config)
        assert "--allowedTools" in cmd
        idx = cmd.index("--allowedTools")
        assert cmd[idx + 1] == "Bash Edit Read"
        agent.cleanup()

    def test_no_allowed_tools_when_none(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path)
        cmd = agent.get_launch_command(config)
        assert "--allowedTools" not in cmd
        agent.cleanup()

    # -- disallowed_tools → --disallowedTools --

    def test_disallowed_tools(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, disallowed_tools=["WebSearch", "WebFetch"])
        cmd = agent.get_launch_command(config)
        assert "--disallowedTools" in cmd
        idx = cmd.index("--disallowedTools")
        assert cmd[idx + 1] == "WebSearch WebFetch"
        agent.cleanup()

    # -- model → --model --

    def test_model(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, model="opus")
        cmd = agent.get_launch_command(config)
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "opus"
        agent.cleanup()

    def test_no_model_when_none(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path)
        cmd = agent.get_launch_command(config)
        assert "--model" not in cmd
        agent.cleanup()

    # -- permissions → --dangerously-skip-permissions --

    def test_permissionless(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, permissions="permissionless")
        cmd = agent.get_launch_command(config)
        assert "--dangerously-skip-permissions" in cmd
        agent.cleanup()

    def test_suggest_permissions(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, permissions="suggest")
        cmd = agent.get_launch_command(config)
        assert "--dangerously-skip-permissions" not in cmd
        agent.cleanup()

    # -- session_name → --name --

    def test_session_name(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, session_name="my-session")
        cmd = agent.get_launch_command(config)
        assert "--name" in cmd
        idx = cmd.index("--name")
        assert cmd[idx + 1] == "my-session"
        agent.cleanup()

    # -- max_budget_usd → --max-budget-usd --

    def test_max_budget(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, max_budget_usd=2.50)
        cmd = agent.get_launch_command(config)
        assert "--max-budget-usd" in cmd
        idx = cmd.index("--max-budget-usd")
        assert cmd[idx + 1] == "2.5"
        agent.cleanup()

    def test_no_budget_when_none(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path)
        cmd = agent.get_launch_command(config)
        assert "--max-budget-usd" not in cmd
        agent.cleanup()

    # -- add_dirs → --add-dir --

    def test_add_dirs(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, add_dirs=[Path("/extra1"), Path("/extra2")])
        cmd = agent.get_launch_command(config)
        add_dir_indices = [i for i, x in enumerate(cmd) if x == "--add-dir"]
        assert len(add_dir_indices) == 2
        assert cmd[add_dir_indices[0] + 1] == "/extra1"
        assert cmd[add_dir_indices[1] + 1] == "/extra2"
        agent.cleanup()

    # -- mode headless → -p + --output-format json --

    def test_headless_mode(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, mode="headless")
        cmd = agent.get_launch_command(config)
        assert "-p" in cmd
        assert "--output-format" in cmd
        assert "json" in cmd
        agent.cleanup()

    # -- mode interactive → positional task, no -p, no --output-format --

    def test_interactive_mode(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, mode="interactive")
        cmd = agent.get_launch_command(config)
        assert "-p" not in cmd
        assert "--output-format" not in cmd
        assert "build it" in cmd  # task as positional
        agent.cleanup()

    # -- get_environment --

    def test_get_environment_strips_venv(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path)
        env = agent.get_environment(config)
        assert "VIRTUAL_ENV" not in env

    def test_get_environment_sets_factory_model(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path, model="opus")
        env = agent.get_environment(config)
        assert env.get("FACTORY_MODEL") == "opus"

    # -- parse_output --

    def test_parse_output_json(self) -> None:
        agent = ClaudeCodeAgent()
        data = {
            "result": "Build complete",
            "usage": {"input_tokens": 100, "output_tokens": 50,
                      "cache_read_input_tokens": 20, "cache_creation_input_tokens": 10},
            "cost_usd": 0.01, "duration_ms": 5000, "num_turns": 3, "model": "opus",
        }
        result = agent.parse_output(json.dumps(data), 0)
        assert result.output == "Build complete"
        assert result.return_code == 0
        assert result.usage is not None
        assert result.usage.input_tokens == 100
        assert result.usage.cache_read_tokens == 20
        assert result.usage.cache_creation_tokens == 10

    def test_parse_output_raw_fallback(self) -> None:
        agent = ClaudeCodeAgent()
        result = agent.parse_output("raw text", 0)
        assert result.output == "raw text"
        assert result.usage is None

    def test_parse_output_non_zero(self) -> None:
        agent = ClaudeCodeAgent()
        result = agent.parse_output("error", 1)
        assert result.return_code == 1

    # -- preflight --

    def test_preflight(self) -> None:
        ClaudeCodeAgent().preflight()

    # -- cleanup --

    def test_cleanup(self, tmp_path: Path) -> None:
        agent = ClaudeCodeAgent()
        config = self._make_config(tmp_path)
        agent.get_launch_command(config)
        assert len(agent._prompt_files) == 1
        f = agent._prompt_files[0]
        assert f.exists()
        agent.cleanup()
        assert not f.exists()
        assert len(agent._prompt_files) == 0


# ---------------------------------------------------------------------------
# CodexAgent — semantic field mapping
# ---------------------------------------------------------------------------

class TestCodexAgent:
    def _make_config(self, tmp_path: Path, **kwargs) -> AgentLaunchConfig:
        defaults: dict = dict(project_path=tmp_path, append_system_prompt="You are a coder", task="code it")
        defaults.update(kwargs)
        return AgentLaunchConfig(**defaults)

    # -- system_prompt → inline in prompt --

    def test_system_prompt_inline(self, tmp_path: Path) -> None:
        agent = CodexAgent()
        config = self._make_config(tmp_path, system_prompt="SYSTEM", append_system_prompt=None)
        cmd = agent.get_launch_command(config)
        full_prompt = cmd[2]  # codex exec <prompt>
        assert "SYSTEM" in full_prompt
        assert "code it" in full_prompt

    # -- append_system_prompt → inline in prompt --

    def test_append_system_prompt_inline(self, tmp_path: Path) -> None:
        agent = CodexAgent()
        config = self._make_config(tmp_path, append_system_prompt="APPENDED")
        cmd = agent.get_launch_command(config)
        full_prompt = cmd[2]
        assert "APPENDED" in full_prompt
        assert "## Current Task" in full_prompt
        assert "code it" in full_prompt

    def test_both_prompts_concatenated(self, tmp_path: Path) -> None:
        agent = CodexAgent()
        config = self._make_config(tmp_path, system_prompt="SYS", append_system_prompt="APP")
        cmd = agent.get_launch_command(config)
        full_prompt = cmd[2]
        assert "SYS" in full_prompt
        assert "APP" in full_prompt

    # -- allowed_tools → not supported (no flag emitted) --

    def test_allowed_tools_ignored(self, tmp_path: Path) -> None:
        """Codex has no tool filtering — allowed_tools is accepted but produces no flag."""
        agent = CodexAgent()
        config = self._make_config(tmp_path, allowed_tools=["Bash"])
        cmd = agent.get_launch_command(config)
        assert "--allowedTools" not in cmd
        # No error — just silently ignored

    # -- model → --model --

    def test_model(self, tmp_path: Path) -> None:
        agent = CodexAgent()
        config = self._make_config(tmp_path, model="o4-mini")
        cmd = agent.get_launch_command(config)
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "o4-mini"

    # -- permissions → --dangerously-bypass-approvals-and-sandbox --

    def test_permissionless(self, tmp_path: Path) -> None:
        agent = CodexAgent()
        config = self._make_config(tmp_path, permissions="permissionless")
        cmd = agent.get_launch_command(config)
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd

    def test_suggest_permissions(self, tmp_path: Path) -> None:
        agent = CodexAgent()
        config = self._make_config(tmp_path, permissions="suggest")
        cmd = agent.get_launch_command(config)
        assert "--dangerously-bypass-approvals-and-sandbox" not in cmd

    # -- add_dirs → --add-dir --

    def test_add_dirs(self, tmp_path: Path) -> None:
        agent = CodexAgent()
        config = self._make_config(tmp_path, add_dirs=[Path("/extra")])
        cmd = agent.get_launch_command(config)
        assert "--add-dir" in cmd

    # -- mode headless → codex exec --

    def test_headless_mode(self, tmp_path: Path) -> None:
        agent = CodexAgent()
        config = self._make_config(tmp_path, mode="headless")
        cmd = agent.get_launch_command(config)
        assert cmd[0] == "codex"
        assert cmd[1] == "exec"

    # -- mode interactive → codex (no exec) --

    def test_interactive_mode_still_uses_exec(self, tmp_path: Path) -> None:
        """Codex always uses `codex exec` — interactive codex requires a TTY."""
        agent = CodexAgent()
        config = self._make_config(tmp_path, mode="interactive")
        cmd = agent.get_launch_command(config)
        assert cmd[0] == "codex"
        assert cmd[1] == "exec"

    # -- get_environment --

    def test_get_environment_maps_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_API_KEY", "test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        agent = CodexAgent()
        config = self._make_config(tmp_path)
        env = agent.get_environment(config)
        assert env.get("OPENAI_API_KEY") == "test-key"

    # -- parse_output --

    def test_parse_output(self) -> None:
        agent = CodexAgent()
        result = agent.parse_output("output", 0)
        assert result.output == "output"
        assert result.usage is None

    def test_parse_output_non_zero(self) -> None:
        agent = CodexAgent()
        result = agent.parse_output("err", 2)
        assert result.return_code == 2

    # -- preflight --

    def test_preflight_dry_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_CODEX_DRY_RUN", "1")
        CodexAgent().preflight()

    def test_preflight_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        import factory.runners.codex as m
        m._auth_checked = False
        CodexAgent().preflight()

    def test_preflight_no_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("FACTORY_CODEX_DRY_RUN", raising=False)
        import factory.runners.codex as m
        m._auth_checked = False
        with pytest.raises(CodexAuthError):
            CodexAgent().preflight()


# ---------------------------------------------------------------------------
# AgentResult
# ---------------------------------------------------------------------------

class TestAgentResult:
    def test_basic(self) -> None:
        r = AgentResult(output="hello", return_code=0)
        assert r.usage is None

    def test_with_usage(self) -> None:
        from factory.models import AgentUsage
        r = AgentResult(output="ok", return_code=0, usage=AgentUsage(input_tokens=10, output_tokens=5))
        assert r.usage is not None
        assert r.usage.input_tokens == 10


# ---------------------------------------------------------------------------
# RunnerName / RUNNER_CHOICES
# ---------------------------------------------------------------------------

class TestRunnerName:
    def test_choices_derived_from_literal(self) -> None:
        from typing import get_args
        assert RUNNER_CHOICES == get_args(RunnerName)
        assert "claude" in RUNNER_CHOICES
        assert "codex" in RUNNER_CHOICES


# ---------------------------------------------------------------------------
# get_runner() → AgentRunner
# ---------------------------------------------------------------------------

class TestGetRunner:
    def test_default_claude(self) -> None:
        r = get_runner()
        assert isinstance(r, AgentRunner)
        assert r.name == "claude"

    def test_explicit_codex(self) -> None:
        r = get_runner("codex")
        assert r.name == "codex"

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            get_runner("unknown")

    def test_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER", "codex")
        assert get_runner().name == "codex"

    def test_explicit_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FACTORY_RUNNER", "codex")
        assert get_runner("claude").name == "claude"


# ---------------------------------------------------------------------------
# AgentRunner compositor
# ---------------------------------------------------------------------------

class TestAgentRunnerCompositor:
    def test_name(self) -> None:
        assert AgentRunner(ClaudeCodeAgent()).name == "claude"

    def test_from_legacy_name(self) -> None:
        from factory.runners.bob import BobRunner
        assert AgentRunner.from_legacy(BobRunner()).name == "bob"

    def test_headless_maps_prompt_to_append(self) -> None:
        """Legacy headless(prompt=...) maps to append_system_prompt."""
        agent = ClaudeCodeAgent()
        configs: list[AgentLaunchConfig] = []
        orig = agent.get_launch_command

        def spy(c: AgentLaunchConfig) -> list[str]:
            configs.append(c)
            return ["echo"]

        agent.get_launch_command = spy  # type: ignore[assignment]
        mock_rt = MagicMock()

        import asyncio

        async def mock_exec(*a, **kw):  # type: ignore[no-untyped-def]
            return ("out", 0)

        mock_rt.execute = mock_exec
        r = AgentRunner(agent, mock_rt)
        asyncio.run(r.headless("my prompt", "my task", Path("/tmp")))

        assert len(configs) == 1
        assert configs[0].append_system_prompt == "my prompt"
        assert configs[0].task == "my task"
        assert configs[0].mode == "headless"

    def test_interactive_sets_mode(self) -> None:
        agent = ClaudeCodeAgent()
        configs: list[AgentLaunchConfig] = []

        def spy(c: AgentLaunchConfig) -> list[str]:
            configs.append(c)
            return ["echo"]

        agent.get_launch_command = spy  # type: ignore[assignment]
        mock_rt = MagicMock()
        mock_rt.execute_interactive = MagicMock(return_value=0)
        AgentRunner(agent, mock_rt).interactive_run("p", "t", Path("/tmp"))
        assert configs[0].mode == "interactive"

    def test_cleanup_called(self) -> None:
        agent = ClaudeCodeAgent()
        mock_rt = MagicMock()

        import asyncio

        async def mock_exec(*a, **kw):  # type: ignore[no-untyped-def]
            return ("out", 0)

        mock_rt.execute = mock_exec
        asyncio.run(AgentRunner(agent, mock_rt).headless("p", "t", Path("/tmp")))
        assert len(agent._prompt_files) == 0
