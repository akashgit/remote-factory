"""Tests for the re:factory agent workspace setup and session management."""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from factory.refactory import (
    CLAUDE_MD_CONTENT,
    SETTINGS_JSON,
    get_session_id,
    save_session_id,
    setup_workspace,
)


@pytest.fixture
def mock_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        "factory.refactory.WORKSPACE_DIR", tmp_path / ".factory" / "refactory"
    )
    monkeypatch.setattr(
        "factory.refactory.SESSION_FILE",
        tmp_path / ".factory" / "refactory-session.json",
    )
    return tmp_path


# ── setup_workspace ──────────────────────────────────────────────


class TestSetupWorkspace:
    def test_creates_directories(self, mock_home: Path) -> None:
        setup_workspace()
        workspace = mock_home / ".factory" / "refactory"
        assert workspace.is_dir()
        assert (workspace / ".claude").is_dir()
        assert (workspace / ".claude" / "commands").is_dir()

    def test_writes_settings_json(self, mock_home: Path) -> None:
        setup_workspace()
        settings = mock_home / ".factory" / "refactory" / ".claude" / "settings.json"
        assert settings.exists()
        data = json.loads(settings.read_text())
        assert data == SETTINGS_JSON
        assert "factory" in data["mcpServers"]

    def test_writes_claude_md(self, mock_home: Path) -> None:
        setup_workspace()
        claude_md = mock_home / ".factory" / "refactory" / "CLAUDE.md"
        assert claude_md.exists()
        assert claude_md.read_text() == CLAUDE_MD_CONTENT

    def test_copies_skills(self, mock_home: Path) -> None:
        setup_workspace()
        commands_dir = mock_home / ".factory" / "refactory" / ".claude" / "commands"
        skills_src = Path(__file__).parent.parent / "factory" / "agents" / "skills"
        expected = list(skills_src.glob("*.md"))
        assert len(expected) > 0, "No skill source files found"
        for skill in expected:
            assert (commands_dir / skill.name).exists(), f"Missing skill: {skill.name}"

    def test_idempotent(self, mock_home: Path) -> None:
        ws1 = setup_workspace()
        ws2 = setup_workspace()
        assert ws1 == ws2
        settings = mock_home / ".factory" / "refactory" / ".claude" / "settings.json"
        assert json.loads(settings.read_text()) == SETTINGS_JSON


# ── Session ID ───────────────────────────────────────────────────


class TestSessionId:
    def test_creates_new(self, mock_home: Path) -> None:
        session_file = mock_home / ".factory" / "refactory-session.json"
        assert not session_file.exists()
        sid = get_session_id()
        assert isinstance(sid, str)
        assert len(sid) == 36  # UUID with dashes
        assert sid.count("-") == 4
        assert session_file.exists()

    def test_returns_existing(self, mock_home: Path) -> None:
        sid1 = get_session_id()
        sid2 = get_session_id()
        assert sid1 == sid2

    def test_reset(self, mock_home: Path) -> None:
        sid1 = get_session_id()
        sid2 = get_session_id(reset=True)
        assert sid1 != sid2
        assert len(sid2) == 36

    def test_save_roundtrip(self, mock_home: Path) -> None:
        custom_id = "abcdef1234567890abcdef1234567890"
        save_session_id(custom_id)
        assert get_session_id() == custom_id

    def test_corrupt_json_generates_new(self, mock_home: Path) -> None:
        session_file = mock_home / ".factory" / "refactory-session.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text("{corrupt json!!")
        sid = get_session_id()
        assert isinstance(sid, str)
        assert len(sid) == 36


# ── Agent role registration ──────────────────────────────────────


class TestAgentRegistration:
    def test_refactory_role_in_agent_role(self) -> None:
        from factory.agents.runner import AgentRole

        assert "refactory" in get_args(AgentRole)

    def test_refactory_in_agents_yml(self) -> None:
        import yaml

        yml_path = Path(__file__).parent.parent / "factory" / "agents" / "agents.yml"
        data = yaml.safe_load(yml_path.read_text())
        assert "refactory" in data
        assert "model" in data["refactory"]
        assert "tools" in data["refactory"]


# ── CLI integration ──────────────────────────────────────────────


class TestCLIIntegration:
    def test_refactory_subcommand_exists(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["refactory"])
        assert args.command == "refactory"

    def test_refactory_prompt_resolves(self) -> None:
        from factory.agents.runner import resolve_prompt

        prompt = resolve_prompt("refactory")
        assert isinstance(prompt, str)
        assert len(prompt) > 0


# ── cmd_refactory ────────────────────────────────────────────────


class TestCmdRefactory:
    def test_no_claude_returns_error(self, mock_home: Path) -> None:
        from unittest.mock import patch

        from factory.cli import cmd_refactory, build_parser

        parser = build_parser()
        args = parser.parse_args(["refactory"])
        with patch("shutil.which", return_value=None):
            code = cmd_refactory(args)
        assert code == 1

    def test_new_session_no_resume_flag(self, mock_home: Path) -> None:
        from unittest.mock import patch

        from factory.cli import cmd_refactory, build_parser

        parser = build_parser()
        args = parser.parse_args(["refactory"])
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("os.execvp") as mock_exec:
            cmd_refactory(args)

        cmd = mock_exec.call_args[0][1]
        assert "--session-id" in cmd
        assert "--resume" not in cmd
        assert "--append-system-prompt-file" in cmd
        assert "--cwd" not in cmd

    def test_existing_session_has_resume_flag(self, mock_home: Path) -> None:
        from unittest.mock import patch

        from factory.cli import cmd_refactory, build_parser

        save_session_id("existing-uuid")
        parser = build_parser()
        args = parser.parse_args(["refactory"])
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("os.execvp") as mock_exec:
            cmd_refactory(args)

        cmd = mock_exec.call_args[0][1]
        assert "--resume" in cmd

    def test_reset_flag_no_resume(self, mock_home: Path) -> None:
        from unittest.mock import patch

        from factory.cli import cmd_refactory, build_parser

        save_session_id("old-uuid")
        parser = build_parser()
        args = parser.parse_args(["refactory", "--reset"])
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("os.execvp") as mock_exec:
            cmd_refactory(args)

        cmd = mock_exec.call_args[0][1]
        assert "--resume" not in cmd

    def test_model_flag_forwarded(self, mock_home: Path) -> None:
        from unittest.mock import patch

        from factory.cli import cmd_refactory, build_parser

        parser = build_parser()
        args = parser.parse_args(["refactory", "--model", "sonnet"])
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("os.execvp") as mock_exec:
            cmd_refactory(args)

        cmd = mock_exec.call_args[0][1]
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "sonnet"
