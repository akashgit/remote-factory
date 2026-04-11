"""Tests for factory.agents — prompt loading and resolution."""


import pytest

from factory.agents.runner import resolve_prompt, AgentRole, _PROMPTS_DIR


class TestResolvePrompt:
    def test_loads_default_prompt(self):
        prompt = resolve_prompt("researcher")
        assert "Researcher" in prompt
        assert len(prompt) > 100

    def test_all_default_prompts_exist(self):
        roles: list[AgentRole] = ["researcher", "strategist", "evaluator", "reviewer", "archivist"]
        for role in roles:
            prompt = resolve_prompt(role)
            assert len(prompt) > 50, f"Prompt for {role} is too short"

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
        roles: list[AgentRole] = ["researcher", "strategist", "evaluator", "reviewer", "archivist"]
        for role in roles:
            prompt = resolve_prompt(role)
            assert prompt.startswith("# "), f"Prompt for {role} should start with '# '"
