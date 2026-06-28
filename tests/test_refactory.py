"""Tests for the re:factory agent workspace setup and session management."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import get_args
from unittest.mock import patch

import pytest

from factory.refactory import (
    CLAUDE_MD_CONTENT,
    get_session_id,
    save_session_id,
    setup_workspace,
)


# ── setup_workspace ──────────────────────────────────────────────


class TestSetupWorkspace:
    def test_creates_directories(self, tmp_path: Path) -> None:
        setup_workspace(tmp_path)
        workspace = tmp_path / ".refactory"
        assert workspace.is_dir()
        assert (tmp_path / ".claude").is_dir()
        assert (tmp_path / ".claude" / "commands").is_dir()

    def test_writes_settings_json(self, tmp_path: Path) -> None:
        setup_workspace(tmp_path)
        settings = tmp_path / ".claude" / "settings.local.json"
        assert settings.exists()
        data = json.loads(settings.read_text())
        assert "factory" in data["mcpServers"]

    def test_writes_claude_md(self, tmp_path: Path) -> None:
        setup_workspace(tmp_path)
        claude_md = tmp_path / ".refactory" / "CLAUDE.md"
        assert claude_md.exists()
        assert claude_md.read_text() == CLAUDE_MD_CONTENT

    def test_copies_skills(self, tmp_path: Path) -> None:
        setup_workspace(tmp_path)
        commands_dir = tmp_path / ".claude" / "commands"
        skills_src = Path(__file__).parent.parent / "factory" / "agents" / "skills"
        expected = list(skills_src.glob("*.md"))
        assert len(expected) > 0, "No skill source files found"
        for skill in expected:
            assert (commands_dir / skill.name).exists(), f"Missing skill: {skill.name}"

    def test_idempotent(self, tmp_path: Path) -> None:
        ws1 = setup_workspace(tmp_path)
        ws2 = setup_workspace(tmp_path)
        assert ws1 == ws2
        settings = tmp_path / ".claude" / "settings.local.json"
        data = json.loads(settings.read_text())
        assert "factory" in data["mcpServers"]

    def test_copies_hooks(self, tmp_path: Path) -> None:
        setup_workspace(tmp_path)
        sop_dir = tmp_path / ".refactory" / ".claude" / "sop-compact"
        assert sop_dir.is_dir()
        for name in ("pre-compact.sh", "session-start.sh"):
            hook = sop_dir / name
            assert hook.exists(), f"Missing hook: {name}"
            assert hook.stat().st_mode & stat.S_IXUSR, f"Hook not executable: {name}"

    def test_copies_sop(self, tmp_path: Path) -> None:
        setup_workspace(tmp_path)
        sop = tmp_path / ".refactory" / ".claude" / "sop-compact.md"
        assert sop.exists()
        content = sop.read_text()
        assert "re:factory" in content
        assert "Promotion targets" in content

    def test_settings_json_has_hooks(self, tmp_path: Path) -> None:
        setup_workspace(tmp_path)
        settings = tmp_path / ".claude" / "settings.local.json"
        data = json.loads(settings.read_text())
        assert "hooks" in data
        assert "PreCompact" in data["hooks"]
        assert "SessionStart" in data["hooks"]
        pre_cmd = data["hooks"]["PreCompact"][0]["hooks"][0]["command"]
        assert "pre-compact.sh" in pre_cmd
        assert os.path.isabs(pre_cmd)
        session_cmd = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert "session-start.sh" in session_cmd
        assert os.path.isabs(session_cmd)


# ── Session ID ───────────────────────────────────────────────────


class TestSessionId:
    def test_creates_new(self, tmp_path: Path) -> None:
        (tmp_path / ".refactory").mkdir()
        session_file = tmp_path / ".refactory" / "session.json"
        assert not session_file.exists()
        sid = get_session_id(tmp_path)
        assert isinstance(sid, str)
        assert len(sid) == 36
        assert sid.count("-") == 4
        assert session_file.exists()

    def test_returns_existing(self, tmp_path: Path) -> None:
        (tmp_path / ".refactory").mkdir()
        sid1 = get_session_id(tmp_path)
        sid2 = get_session_id(tmp_path)
        assert sid1 == sid2

    def test_reset(self, tmp_path: Path) -> None:
        (tmp_path / ".refactory").mkdir()
        sid1 = get_session_id(tmp_path)
        sid2 = get_session_id(tmp_path, reset=True)
        assert sid1 != sid2
        assert len(sid2) == 36

    def test_save_roundtrip(self, tmp_path: Path) -> None:
        (tmp_path / ".refactory").mkdir()
        custom_id = "abcdef1234567890abcdef1234567890"
        save_session_id(tmp_path, custom_id)
        assert get_session_id(tmp_path) == custom_id

    def test_corrupt_json_generates_new(self, tmp_path: Path) -> None:
        session_file = tmp_path / ".refactory" / "session.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text("{corrupt json!!")
        sid = get_session_id(tmp_path)
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

    def test_refactory_accepts_path_arg(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["refactory", "/some/path"])
        assert args.path == "/some/path"

    def test_refactory_path_default_none(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["refactory"])
        assert args.path is None

    def test_refactory_prompt_resolves(self) -> None:
        from factory.agents.runner import resolve_prompt

        prompt = resolve_prompt("refactory")
        assert isinstance(prompt, str)
        assert len(prompt) > 0


# ── cmd_refactory ────────────────────────────────────────────────


class TestCmdRefactory:
    def test_no_claude_returns_error(self, tmp_path: Path) -> None:
        from factory.cli import cmd_refactory, build_parser

        parser = build_parser()
        args = parser.parse_args(["refactory", str(tmp_path)])
        with patch("shutil.which", return_value=None):
            code = cmd_refactory(args)
        assert code == 1

    def test_new_session_uses_session_id(self, tmp_path: Path) -> None:
        from factory.cli import cmd_refactory, build_parser

        parser = build_parser()
        args = parser.parse_args(["refactory", str(tmp_path)])
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("os.execvp") as mock_exec:
            cmd_refactory(args)

        cmd = mock_exec.call_args[0][1]
        assert "--session-id" in cmd
        assert "--resume" not in cmd
        assert "--append-system-prompt-file" in cmd

    def test_existing_session_uses_resume(self, tmp_path: Path) -> None:
        from factory.cli import cmd_refactory, build_parser

        save_session_id(tmp_path, "existing-uuid")
        parser = build_parser()
        args = parser.parse_args(["refactory", str(tmp_path)])
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("os.execvp") as mock_exec:
            cmd_refactory(args)

        cmd = mock_exec.call_args[0][1]
        assert "--resume" in cmd
        assert "--session-id" not in cmd
        resume_idx = cmd.index("--resume")
        assert cmd[resume_idx + 1] == "existing-uuid"

    def test_reset_flag_uses_session_id(self, tmp_path: Path) -> None:
        from factory.cli import cmd_refactory, build_parser

        save_session_id(tmp_path, "old-uuid")
        parser = build_parser()
        args = parser.parse_args(["refactory", "--reset", str(tmp_path)])
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("os.execvp") as mock_exec:
            cmd_refactory(args)

        cmd = mock_exec.call_args[0][1]
        assert "--session-id" in cmd
        assert "--resume" not in cmd

    def test_model_flag_forwarded(self, tmp_path: Path) -> None:
        from factory.cli import cmd_refactory, build_parser

        parser = build_parser()
        args = parser.parse_args(["refactory", "--model", "sonnet", str(tmp_path)])
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("os.execvp") as mock_exec:
            cmd_refactory(args)

        cmd = mock_exec.call_args[0][1]
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "sonnet"

    def test_default_path_uses_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from factory.cli import cmd_refactory, build_parser

        monkeypatch.chdir(tmp_path)
        parser = build_parser()
        args = parser.parse_args(["refactory"])
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("os.execvp"):
            cmd_refactory(args)

        assert (tmp_path / ".refactory").is_dir()
        assert (tmp_path / ".refactory" / "session.json").exists()
