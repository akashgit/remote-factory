"""Tests for factory.agents — prompt loading and resolution."""

from pathlib import Path

import pytest

from factory.agents.runner import resolve_prompt, AgentRole, _PROMPTS_DIR


# Path to the project root (parent of factory/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestResolvePrompt:
    def test_loads_default_prompt(self):
        prompt = resolve_prompt("researcher")
        assert "Researcher" in prompt
        assert len(prompt) > 100

    def test_all_default_prompts_exist(self):
        roles: list[AgentRole] = ["researcher", "strategist", "evaluator", "reviewer", "archivist", "ceo"]
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
        roles: list[AgentRole] = ["researcher", "strategist", "evaluator", "reviewer", "archivist", "ceo"]
        for role in roles:
            prompt = resolve_prompt(role)
            assert prompt.startswith("# "), f"Prompt for {role} should start with '# '"


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

        async def mock_invoke(role, task, path, *, timeout=600.0, dangerously_skip_permissions=True, model=None):
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

        async def mock_invoke(role, task, path, *, timeout=600.0, dangerously_skip_permissions=True, model=None):
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

        async def mock_invoke(role, task, path, *, timeout=600.0, dangerously_skip_permissions=True, model=None):
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
        from factory.agents.runner import invoke_agent

        captured_cmd: list[str] = []

        async def mock_exec(*args, **kwargs):
            captured_cmd.extend(args)
            proc = type("P", (), {
                "communicate": lambda self: (b"ok", b""),
                "returncode": 0,
                "kill": lambda self: None,
                "wait": lambda self: None,
            })()

            async def communicate():
                return (b"ok", b"")
            proc.communicate = communicate
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", mock_exec)

        await invoke_agent("researcher", "test task", tmp_path, model="claude-opus-4-6")
        assert "--model" in captured_cmd
        model_idx = captured_cmd.index("--model")
        assert captured_cmd[model_idx + 1] == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_no_model_flag_when_none(self, tmp_path, monkeypatch):
        """invoke_agent omits --model when model is None."""
        from factory.agents.runner import invoke_agent

        captured_cmd: list[str] = []

        async def mock_exec(*args, **kwargs):
            captured_cmd.extend(args)
            proc = type("P", (), {"returncode": 0})()

            async def communicate():
                return (b"ok", b"")
            proc.communicate = communicate
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", mock_exec)

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


