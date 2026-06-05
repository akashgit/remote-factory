"""Tests for factory.agents — prompt loading and resolution."""

import json
from pathlib import Path

import pytest

from factory.agents.runner import (
    resolve_prompt,
    AgentRole,
    _PROMPTS_DIR,
    ConsecutiveAgentFailureError,
    reset_failure_counter,
)


# Path to the project root (parent of factory/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestResolvePrompt:
    def test_loads_default_prompt(self):
        prompt = resolve_prompt("researcher")
        assert "Researcher" in prompt
        assert len(prompt) > 100

    def test_all_default_prompts_exist(self):
        roles: list[AgentRole] = [
            "researcher", "strategist", "evaluator", "reviewer",
            "archivist", "distiller", "ceo", "failure_analyst",
        ]
        for role in roles:
            prompt = resolve_prompt(role)
            assert len(prompt) > 50, f"Prompt for {role} is too short"

    def test_ceo_prompt_loads(self):
        prompt = resolve_prompt("ceo")
        assert "Factory CEO Agent" in prompt
        assert "factory agent" in prompt

    def test_project_override_takes_priority(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        agents_dir = project / ".factory" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "researcher.md").write_text("# Custom Researcher\nProject-specific override.")

        prompt = resolve_prompt("researcher", project)
        assert "Custom Researcher" in prompt
        assert "Project-specific override" in prompt

    def test_falls_back_to_default_when_no_override(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        # No .factory/agents/ directory
        prompt = resolve_prompt("researcher", project)
        assert "Researcher" in prompt  # default prompt

    def test_missing_role_raises_error(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        with pytest.raises(FileNotFoundError):
            resolve_prompt("nonexistent_role", project)  # type: ignore[arg-type]

    def test_prompts_dir_exists(self):
        assert _PROMPTS_DIR.exists()
        assert _PROMPTS_DIR.is_dir()

    def test_each_prompt_has_header(self):
        roles: list[AgentRole] = [
            "researcher", "strategist", "evaluator", "reviewer",
            "archivist", "distiller", "ceo", "failure_analyst",
        ]
        for role in roles:
            prompt = resolve_prompt(role)
            assert prompt.startswith("# "), f"Prompt for {role} should start with '# '"

    def test_failure_analyst_prompt_content(self):
        prompt = resolve_prompt("failure_analyst")
        assert "Failure Analyst" in prompt
        assert "failure_analysis.md" in prompt
        assert "Per-Instance Classification" in prompt


class TestResearcherPromptModes:
    """Verify the researcher prompt contains both Discovery and Research modes."""

    def test_has_mode_1_discovery(self):
        prompt = resolve_prompt("researcher")
        assert "Mode 1" in prompt
        assert "Discovery" in prompt

    def test_has_mode_2_research(self):
        prompt = resolve_prompt("researcher")
        assert "Mode 2" in prompt
        assert "Research" in prompt

    def test_has_discovery_output(self):
        prompt = resolve_prompt("researcher")
        assert "Output (Discovery)" in prompt

    def test_has_research_output(self):
        prompt = resolve_prompt("researcher")
        assert "Output (Research)" in prompt


class TestInvokeAgentsParallel:
    @pytest.mark.asyncio
    async def test_runs_multiple_agents(self, tmp_path, monkeypatch):
        """invoke_agents_parallel runs multiple agents concurrently."""
        from factory.agents.runner import invoke_agents_parallel

        call_count = 0

        async def mock_invoke(role, task, path, *, timeout=600.0, dangerously_skip_permissions=True, model=None, runner_name=None, _track_failures=True, tmux_persist=False):
            nonlocal call_count
            call_count += 1
            return (f"output-{role}", 0)

        monkeypatch.setattr("factory.agents.runner.invoke_agent", mock_invoke)

        tasks: list[tuple[AgentRole, str]] = [
            ("builder", "task 1"),
            ("evaluator", "task 2"),
        ]
        results = await invoke_agents_parallel(tasks, tmp_path)
        assert len(results) == 2
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_returns_all_results(self, tmp_path, monkeypatch):
        """invoke_agents_parallel returns results from all agents."""
        from factory.agents.runner import invoke_agents_parallel

        async def mock_invoke(role, task, path, *, timeout=600.0, dangerously_skip_permissions=True, model=None, runner_name=None, _track_failures=True, tmux_persist=False):
            return (f"output-{role}", 0)

        monkeypatch.setattr("factory.agents.runner.invoke_agent", mock_invoke)

        tasks: list[tuple[AgentRole, str]] = [
            ("builder", "task 1"),
            ("reviewer", "task 2"),
            ("evaluator", "task 3"),
        ]
        results = await invoke_agents_parallel(tasks, tmp_path)
        assert len(results) == 3
        assert all(rc == 0 for _, rc in results)

    @pytest.mark.asyncio
    async def test_passes_model_to_invoke_agent(self, tmp_path, monkeypatch):
        """invoke_agents_parallel passes model kwarg through to invoke_agent."""
        from factory.agents.runner import invoke_agents_parallel

        captured_models: list[str | None] = []

        async def mock_invoke(role, task, path, *, timeout=600.0, dangerously_skip_permissions=True, model=None, runner_name=None, _track_failures=True, tmux_persist=False):
            captured_models.append(model)
            return (f"output-{role}", 0)

        monkeypatch.setattr("factory.agents.runner.invoke_agent", mock_invoke)

        tasks: list[tuple[AgentRole, str]] = [("builder", "task 1"), ("evaluator", "task 2")]
        await invoke_agents_parallel(tasks, tmp_path, model="claude-opus-4-6")
        assert all(m == "claude-opus-4-6" for m in captured_models)


class TestInvokeAgentModel:
    @pytest.mark.asyncio
    async def test_model_flag_in_subprocess_cmd(self, tmp_path, monkeypatch):
        """invoke_agent includes --model in subprocess command when model is set."""
        from unittest.mock import AsyncMock, patch

        from factory.agents.runner import invoke_agent

        captured_cmd: list[str] = []

        async def mock_exec(*args, **kwargs):
            captured_cmd.extend(args)
            proc = AsyncMock()
            proc.returncode = 0
            return proc

        with patch(
            "factory.runners._subprocess.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b"ok", b"")

            monkeypatch.setattr("factory.runners._subprocess.asyncio.create_subprocess_exec", mock_exec)

            await invoke_agent("researcher", "test task", tmp_path, model="claude-opus-4-6")
            assert "--model" in captured_cmd
            model_idx = captured_cmd.index("--model")
            assert captured_cmd[model_idx + 1] == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_no_model_flag_when_none(self, tmp_path, monkeypatch):
        """invoke_agent omits --model when model is None."""
        from unittest.mock import AsyncMock, patch

        from factory.agents.runner import invoke_agent

        captured_cmd: list[str] = []

        async def mock_exec(*args, **kwargs):
            captured_cmd.extend(args)
            proc = AsyncMock()
            proc.returncode = 0
            return proc

        with patch(
            "factory.runners._subprocess.stream_subprocess", new_callable=AsyncMock
        ) as mock_stream:
            mock_stream.return_value = (b"ok", b"")

            monkeypatch.setattr("factory.runners._subprocess.asyncio.create_subprocess_exec", mock_exec)

            await invoke_agent("researcher", "test task", tmp_path, model=None)
            assert "--model" not in captured_cmd


class TestResolveModel:
    def test_flag_takes_precedence_over_env(self, monkeypatch):
        """CLI flag overrides FACTORY_MODEL env var."""
        import argparse
        from factory.cli import _resolve_model

        monkeypatch.setenv("FACTORY_MODEL", "claude-sonnet-4-6")
        args = argparse.Namespace(model="claude-opus-4-6")
        assert _resolve_model(args) == "claude-opus-4-6"

    def test_env_var_used_when_no_flag(self, monkeypatch):
        """FACTORY_MODEL env var is used when --model is not set."""
        import argparse
        from factory.cli import _resolve_model

        monkeypatch.setenv("FACTORY_MODEL", "claude-opus-4-6")
        args = argparse.Namespace(model=None)
        assert _resolve_model(args) == "claude-opus-4-6"

    def test_returns_none_when_neither_set(self, monkeypatch):
        """Returns None when neither flag nor env var is set."""
        import argparse
        from factory.cli import _resolve_model

        monkeypatch.delenv("FACTORY_MODEL", raising=False)
        args = argparse.Namespace(model=None)
        assert _resolve_model(args) is None

    def test_empty_string_flag_falls_through_to_env(self, monkeypatch):
        """Empty string flag falls through to env var."""
        import argparse
        from factory.cli import _resolve_model

        monkeypatch.setenv("FACTORY_MODEL", "claude-opus-4-6")
        args = argparse.Namespace(model="")
        assert _resolve_model(args) == "claude-opus-4-6"

    def test_whitespace_only_flag_falls_through_to_env(self, monkeypatch):
        """Whitespace-only flag falls through to env var."""
        import argparse
        from factory.cli import _resolve_model

        monkeypatch.setenv("FACTORY_MODEL", "claude-opus-4-6")
        args = argparse.Namespace(model="   ")
        assert _resolve_model(args) == "claude-opus-4-6"

    def test_missing_model_attr_returns_none(self, monkeypatch):
        """No model attribute on args returns None."""
        import argparse
        from factory.cli import _resolve_model

        monkeypatch.delenv("FACTORY_MODEL", raising=False)
        args = argparse.Namespace()
        assert _resolve_model(args) is None


class TestConsecutiveFailureAbort:
    """Tests for consecutive agent failure tracking and abort."""

    def setup_method(self):
        """Reset the failure counter before each test."""
        reset_failure_counter()

    def teardown_method(self):
        """Reset the failure counter after each test."""
        reset_failure_counter()

    @pytest.mark.asyncio
    async def test_success_resets_counter(self, tmp_path, monkeypatch):
        """Successful agent invocation resets the failure counter."""
        import factory.agents.runner as runner_module
        from factory.agents.runner import invoke_agent

        (tmp_path / ".factory").mkdir()

        # Mock the runner at the point where it's imported in runner.py
        class MockRunner:
            name = "claude"
            async def headless(self, *args, **kwargs):
                from factory.models import AgentRunResult
                return AgentRunResult(stdout="success", return_code=0)

        monkeypatch.setattr(runner_module, "get_runner", lambda *args, **kwargs: MockRunner())

        # Set a non-zero failure count
        runner_module._consecutive_failures = 1

        await invoke_agent("researcher", "test", tmp_path)

        # Should be reset to 0 after success
        assert runner_module._consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_failure_increments_counter(self, tmp_path, monkeypatch):
        """Failed agent invocation increments the failure counter."""
        import factory.agents.runner as runner_module
        from factory.agents.runner import invoke_agent

        (tmp_path / ".factory").mkdir()

        class MockRunner:
            name = "claude"
            async def headless(self, *args, **kwargs):
                from factory.models import AgentRunResult
                return AgentRunResult(stdout="error output", return_code=1)

        monkeypatch.setattr(runner_module, "get_runner", lambda *args, **kwargs: MockRunner())

        # Start at 0
        assert runner_module._consecutive_failures == 0

        await invoke_agent("researcher", "test", tmp_path)

        # Should be incremented to 1
        assert runner_module._consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_abort_after_threshold(self, tmp_path, monkeypatch):
        """Abort with error after 2 consecutive failures."""
        import factory.agents.runner as runner_module
        from factory.agents.runner import invoke_agent

        (tmp_path / ".factory").mkdir()

        class MockRunner:
            name = "claude"
            async def headless(self, *args, **kwargs):
                from factory.models import AgentRunResult
                return AgentRunResult(stdout="error", return_code=1)

        monkeypatch.setattr(runner_module, "get_runner", lambda *args, **kwargs: MockRunner())

        # First failure - should not raise
        await invoke_agent("researcher", "test", tmp_path)
        assert runner_module._consecutive_failures == 1

        # Second failure - should raise ConsecutiveAgentFailureError
        with pytest.raises(ConsecutiveAgentFailureError) as exc_info:
            await invoke_agent("strategist", "test", tmp_path)

        assert exc_info.value.failure_count == 2
        assert exc_info.value.last_agent == "strategist"
        assert "consecutive agent spawn failures" in str(exc_info.value)
        assert "events.jsonl" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_abort_emits_event(self, tmp_path, monkeypatch):
        """Abort emits cycle.aborted event."""
        import factory.agents.runner as runner_module
        from factory.agents.runner import invoke_agent

        (tmp_path / ".factory").mkdir()

        class MockRunner:
            name = "claude"
            async def headless(self, *args, **kwargs):
                from factory.models import AgentRunResult
                return AgentRunResult(stdout="error", return_code=1)

        monkeypatch.setattr(runner_module, "get_runner", lambda *args, **kwargs: MockRunner())

        # First failure
        await invoke_agent("researcher", "test", tmp_path)

        # Second failure - triggers abort
        with pytest.raises(ConsecutiveAgentFailureError):
            await invoke_agent("strategist", "test", tmp_path)

        # Check event was emitted
        events_file = tmp_path / ".factory" / "events.jsonl"
        assert events_file.exists()

        events = [json.loads(line) for line in events_file.read_text().splitlines()]
        abort_events = [e for e in events if e["type"] == "cycle.aborted"]
        assert len(abort_events) == 1

        abort_event = abort_events[0]
        assert abort_event["data"]["reason"] == "consecutive_agent_failures"
        assert abort_event["data"]["failure_count"] == 2
        assert abort_event["data"]["last_agent"] == "strategist"

    @pytest.mark.asyncio
    async def test_exception_also_increments_counter(self, tmp_path, monkeypatch):
        """Exception during agent invocation also increments the failure counter."""
        import factory.agents.runner as runner_module
        from factory.agents.runner import invoke_agent

        (tmp_path / ".factory").mkdir()

        class MockRunner:
            name = "claude"
            async def headless(self, *args, **kwargs):
                raise RuntimeError("Connection failed")

        monkeypatch.setattr(runner_module, "get_runner", lambda *args, **kwargs: MockRunner())

        # First failure via exception
        stdout, code = await invoke_agent("researcher", "test", tmp_path)
        assert code == 1
        assert "Error:" in stdout
        assert runner_module._consecutive_failures == 1

    def test_reset_failure_counter(self):
        """reset_failure_counter resets the counter to 0."""
        import factory.agents.runner as runner_module

        runner_module._consecutive_failures = 5
        reset_failure_counter()
        assert runner_module._consecutive_failures == 0

    def test_error_message_is_actionable(self):
        """Error message provides actionable guidance."""
        error = ConsecutiveAgentFailureError(2, "researcher")
        msg = str(error)

        assert "2 consecutive" in msg
        assert "researcher" in msg
        assert "events.jsonl" in msg
        assert "BOBSHELL_API_KEY" in msg  # hint about the common cause


class TestCeoPromptNoBackgroundSpawning:
    """Regression tests: CEO prompt must not suggest background subagent spawning.

    The CEO historically invented a broken pattern: spawning `factory agent`
    in the background and polling via `tail -f` for output. This doesn't work
    and causes double-spend. These tests ensure the prompt forbids this pattern.
    """

    def test_no_background_ampersand_after_factory_agent(self):
        """CEO prompt must not show `factory agent ... &` pattern."""
        prompt = resolve_prompt("ceo")
        # Look for patterns like: factory agent ... &
        # We want to ensure no code block shows backgrounding via &
        import re
        # Match lines that start factory agent and end with &
        pattern = r"factory\s+agent\s+[^`\n]+\s+&\s*$"
        matches = re.findall(pattern, prompt, re.MULTILINE)
        # The only & should be in the "Forbidden pattern" example showing what NOT to do
        # That example has a comment after it: "# Background spawn"
        for match in matches:
            assert "WRONG" in prompt[prompt.find(match) - 50:prompt.find(match)], \
                f"Found `factory agent ... &` without 'WRONG' context: {match}"

    def test_no_tail_f_for_agent_output(self):
        """CEO prompt must not suggest `tail -f` for agent log output."""
        prompt = resolve_prompt("ceo")
        import re
        # Find all tail -f occurrences
        pattern = r"tail\s+-[fF]\s+\S+"
        matches = re.findall(pattern, prompt)
        # All matches should be in a "Forbidden" or "WRONG" context
        for match in matches:
            context_start = max(0, prompt.find(match) - 100)
            context = prompt[context_start:prompt.find(match) + len(match)]
            assert any(marker in context for marker in ["WRONG", "Forbidden", "do not"]), \
                f"Found `tail -f` without forbidden context: {match}"

    def test_has_synchronous_only_rule(self):
        """CEO prompt must explicitly state subagent calls are synchronous."""
        prompt = resolve_prompt("ceo")
        assert "SYNCHRONOUS" in prompt or "synchronous" in prompt
        assert "blocking" in prompt.lower()

    def test_playbook_forbids_background_spawning(self):
        """CEO playbook must have a DON'T rule against background spawning."""
        playbook_path = _PROJECT_ROOT / "factory" / "agents" / "playbooks" / "ceo.md"
        playbook = playbook_path.read_text()
        assert "background" in playbook.lower()
        assert "DON'T" in playbook or "Don't" in playbook
        # Should mention the consequence: double-spend
        assert "double" in playbook.lower()

