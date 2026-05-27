"""Tests for plugin agent generation and sync."""

import pytest
import yaml

from factory.agents.plugin import (
    AgentMeta,
    _READ_ONLY_ROLES,
    _WORKSPACE_WRITE_ROLES,
    _sandbox_mode,
    check_agents_in_sync,
    check_codex_agents_in_sync,
    generate_agent_content,
    generate_codex_agent_toml,
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
            # Generated content may have playbook injected, so check source is in it
            assert source in generated, (
                f"{role}: generated file does not include source prompt"
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

        rc = cmd_install(Namespace(role=None, runner="claude"))
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

        rc = cmd_install(Namespace(role="builder", runner="claude"))
        assert rc == 0
        agents_dir = tmp_path / ".claude" / "agents"
        assert (agents_dir / "factory-builder.md").exists()
        assert not (agents_dir / "factory-ceo.md").exists()

    def test_rejects_invalid_role(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        from argparse import Namespace

        from factory.cli import cmd_install

        rc = cmd_install(Namespace(role="nonexistent", runner="claude"))
        assert rc == 1


class TestSandboxMode:
    def test_read_only_roles(self):
        for role in _READ_ONLY_ROLES:
            assert _sandbox_mode(role) == "read-only"

    def test_workspace_write_roles(self):
        for role in _WORKSPACE_WRITE_ROLES:
            assert _sandbox_mode(role) == "workspace-write"

    def test_all_known_roles_covered(self):
        config = load_agent_config()
        for role in config:
            mode = _sandbox_mode(role)
            assert mode in ("read-only", "workspace-write"), (
                f"{role} has unexpected sandbox mode: {mode}"
            )

    def test_researcher_is_read_only(self):
        assert _sandbox_mode("researcher") == "read-only"

    def test_builder_is_workspace_write(self):
        assert _sandbox_mode("builder") == "workspace-write"

    def test_ceo_is_workspace_write(self):
        assert _sandbox_mode("ceo") == "workspace-write"


class TestGenerateCodexAgentToml:
    def test_generates_valid_toml_structure(self):
        content = generate_codex_agent_toml("researcher")
        assert 'name = "factory-researcher"' in content
        assert "sandbox_mode" in content
        assert "developer_instructions" in content

    def test_has_generated_comment(self):
        content = generate_codex_agent_toml("builder")
        assert "GENERATED FILE" in content
        assert "factory/agents/prompts/builder.md" in content

    def test_sandbox_mode_matches_role(self):
        for role in ALL_ROLES:
            content = generate_codex_agent_toml(role)
            expected_mode = _sandbox_mode(role)
            assert f'sandbox_mode = "{expected_mode}"' in content, (
                f"{role}: expected sandbox_mode={expected_mode}"
            )

    def test_name_prefixed_with_factory(self):
        for role in ALL_ROLES:
            content = generate_codex_agent_toml(role)
            assert f'name = "factory-{role}"' in content

    def test_contains_prompt_heading(self):
        for role in ALL_ROLES:
            source = (_PROMPTS_DIR / f"{role}.md").read_text()
            first_line = source.strip().splitlines()[0]
            generated = generate_codex_agent_toml(role)
            assert first_line in generated, (
                f"{role}: generated TOML does not include first line of source prompt"
            )

    def test_has_prerequisite_note(self):
        content = generate_codex_agent_toml("builder")
        assert "uv tool install" in content

    def test_unknown_role_raises(self):
        with pytest.raises(ValueError, match="Unknown agent role"):
            generate_codex_agent_toml("nonexistent")

    def test_description_present(self):
        for role in ALL_ROLES:
            content = generate_codex_agent_toml(role)
            assert 'description = "' in content

    def test_multiline_instructions(self):
        content = generate_codex_agent_toml("ceo")
        assert 'developer_instructions = """' in content
        assert content.rstrip().endswith('"""')


class TestCheckCodexAgentsInSync:
    def test_passes_when_all_generated(self, tmp_path):
        config = load_agent_config()
        for role in config:
            (tmp_path / f"{role}.toml").write_text(generate_codex_agent_toml(role))
        assert check_codex_agents_in_sync(tmp_path) == []

    def test_detects_missing_file(self, tmp_path):
        out_of_sync = check_codex_agents_in_sync(tmp_path)
        assert len(out_of_sync) == len(load_agent_config())

    def test_detects_stale_file(self, tmp_path):
        config = load_agent_config()
        for role in config:
            (tmp_path / f"{role}.toml").write_text(generate_codex_agent_toml(role))
        (tmp_path / "builder.toml").write_text("stale content")
        out_of_sync = check_codex_agents_in_sync(tmp_path)
        assert out_of_sync == ["builder"]

    def test_none_dir_returns_empty(self):
        assert check_codex_agents_in_sync(None) == []


class TestCmdInstallCodex:
    def test_installs_codex_agents(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        from argparse import Namespace

        from factory.cli import cmd_install

        rc = cmd_install(Namespace(role=None, runner="codex"))
        assert rc == 0
        agents_dir = tmp_path / ".codex" / "agents"
        for role in ALL_ROLES:
            agent_file = agents_dir / f"factory-{role}.toml"
            assert agent_file.exists(), f"Missing TOML agent file for {role}"
            content = agent_file.read_text()
            assert f'name = "factory-{role}"' in content

    def test_installs_single_codex_role(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        from argparse import Namespace

        from factory.cli import cmd_install

        rc = cmd_install(Namespace(role="builder", runner="codex"))
        assert rc == 0
        agents_dir = tmp_path / ".codex" / "agents"
        assert (agents_dir / "factory-builder.toml").exists()
        assert not (agents_dir / "factory-ceo.toml").exists()

    def test_rejects_invalid_codex_role(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        from argparse import Namespace

        from factory.cli import cmd_install

        rc = cmd_install(Namespace(role="nonexistent", runner="codex"))
        assert rc == 1
