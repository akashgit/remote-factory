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

        async def mock_invoke(role, task, path, *, timeout=600.0, dangerously_skip_permissions=True):
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

        async def mock_invoke(role, task, path, *, timeout=600.0, dangerously_skip_permissions=True):
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


class TestClaudeAgentsResearcher:
    """Verify the .claude/agents/researcher.md subagent definition exists and is valid."""

    def test_subagent_file_exists(self):
        agent_file = _PROJECT_ROOT / ".claude" / "agents" / "researcher.md"
        assert agent_file.exists(), (
            f"Expected .claude/agents/researcher.md at {agent_file}"
        )

    def test_subagent_has_frontmatter(self):
        agent_file = _PROJECT_ROOT / ".claude" / "agents" / "researcher.md"
        content = agent_file.read_text()
        assert content.startswith("---"), "Subagent file should start with YAML frontmatter"
        # Find closing frontmatter delimiter
        end = content.find("---", 3)
        assert end != -1, "Subagent file should have closing frontmatter delimiter"
        frontmatter = content[3:end]
        assert "name:" in frontmatter
        assert "researcher" in frontmatter
        assert "tools:" in frontmatter
        assert "WebSearch" in frontmatter
