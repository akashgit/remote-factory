"""Tests for plugin agent generation and sync."""

import pytest
import yaml

from factory.agents.plugin import (
    AgentMeta,
    check_agents_in_sync,
    generate_agent_content,
    load_agent_config,
)
from factory.agents.runner import AgentRole, _PROMPTS_DIR


ALL_ROLES: list[AgentRole] = [
    "researcher", "strategist", "builder", "reviewer",
    "evaluator", "archivist", "distiller", "ceo", "failure_analyst",
]


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from a generated markdown file."""
    assert content.startswith("---\n")
    end = content.index("---\n", 4)
    return yaml.safe_load(content[4:end])


class TestLoadAgentConfig:
    def test_covers_all_roles(self):
        config = load_agent_config()
        for role in ALL_ROLES:
            assert role in config, f"Missing config for {role}"

    def test_includes_failure_analyst(self):
        assert "failure_analyst" in load_agent_config()

    def test_all_entries_are_agent_meta(self):
        for role, meta in load_agent_config().items():
            assert isinstance(meta, AgentMeta), f"{role} config is not AgentMeta"

    def test_ceo_uses_opus(self):
        assert load_agent_config()["ceo"].model == "opus"

    def test_non_ceo_agents_use_sonnet(self):
        for role, meta in load_agent_config().items():
            if role != "ceo":
                assert meta.model == "sonnet", f"{role} should use sonnet, got {meta.model}"

    def test_builder_has_edit_write(self):
        tools = load_agent_config()["builder"].tools
        assert "Edit" in tools
        assert "Write" in tools

    def test_researcher_has_web_tools(self):
        tools = load_agent_config()["researcher"].tools
        assert "WebSearch" in tools
        assert "WebFetch" in tools

    def test_distiller_has_no_bash(self):
        assert "Bash" not in load_agent_config()["distiller"].tools

    def test_all_agents_with_bash_except_distiller(self):
        for role, meta in load_agent_config().items():
            if role == "distiller":
                assert "Bash" not in meta.tools
            else:
                assert "Bash" in meta.tools, f"{role} should have Bash"

    def test_only_includes_roles_with_prompts(self):
        config = load_agent_config()
        for role in config:
            assert (_PROMPTS_DIR / f"{role}.md").exists(), (
                f"{role} in config but no prompt file"
            )


class TestGenerateAgentContent:
    def test_has_frontmatter(self):
        content = generate_agent_content("researcher")
        assert content.startswith("---\n")
        assert "\n---\n" in content[4:]

    def test_frontmatter_has_required_fields(self):
        for role in ALL_ROLES:
            content = generate_agent_content(role)
            fm = _parse_frontmatter(content)
            assert "name" in fm, f"{role}: missing name"
            assert "description" in fm, f"{role}: missing description"
            assert "model" in fm, f"{role}: missing model"
            assert "tools" in fm, f"{role}: missing tools"

    def test_frontmatter_name_matches_role(self):
        for role in ALL_ROLES:
            fm = _parse_frontmatter(generate_agent_content(role))
            assert fm["name"] == role

    def test_has_generated_comment(self):
        content = generate_agent_content("builder")
        assert "GENERATED FILE" in content
        assert "factory/agents/prompts/builder.md" in content

    def test_has_prerequisite_note(self):
        content = generate_agent_content("builder")
        assert "factory" in content
        assert "uv tool install" in content

    def test_preserves_prompt_content(self):
        for role in ALL_ROLES:
            source = (_PROMPTS_DIR / f"{role}.md").read_text()
            generated = generate_agent_content(role)
            assert generated.endswith(source), (
                f"{role}: generated file does not end with source prompt"
            )

    def test_unknown_role_raises(self):
        with pytest.raises(ValueError, match="Unknown agent role"):
            generate_agent_content("nonexistent")


class TestCheckAgentsInSync:
    def test_passes_when_all_generated(self, tmp_path):
        config = load_agent_config()
        for role in config:
            (tmp_path / f"{role}.md").write_text(generate_agent_content(role))
        assert check_agents_in_sync(tmp_path) == []

    def test_detects_missing_file(self, tmp_path):
        out_of_sync = check_agents_in_sync(tmp_path)
        assert len(out_of_sync) == len(load_agent_config())

    def test_detects_stale_file(self, tmp_path):
        config = load_agent_config()
        for role in config:
            (tmp_path / f"{role}.md").write_text(generate_agent_content(role))
        (tmp_path / "builder.md").write_text("stale content")
        out_of_sync = check_agents_in_sync(tmp_path)
        assert out_of_sync == ["builder"]


class TestCmdInstall:
    def test_installs_all_agents(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        from argparse import Namespace

        from factory.cli import cmd_install

        rc = cmd_install(Namespace(role=None))
        assert rc == 0
        agents_dir = tmp_path / ".claude" / "agents"
        for role in ALL_ROLES:
            agent_file = agents_dir / f"factory-{role}.md"
            assert agent_file.exists(), f"Missing agent file for {role}"
            content = agent_file.read_text()
            assert content.startswith("---\n")

    def test_installs_single_role(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        from argparse import Namespace

        from factory.cli import cmd_install

        rc = cmd_install(Namespace(role="builder"))
        assert rc == 0
        agents_dir = tmp_path / ".claude" / "agents"
        assert (agents_dir / "factory-builder.md").exists()
        assert not (agents_dir / "factory-ceo.md").exists()
