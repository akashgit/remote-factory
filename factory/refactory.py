"""Workspace setup and session management for the re:factory agent."""

from __future__ import annotations

import json
import shutil
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SETTINGS_JSON: dict[str, Any] = {
    "mcpServers": {
        "factory": {
            "command": "factory",
            "args": ["mcp-serve"],
        }
    }
}

CLAUDE_MD_CONTENT = """\
# re:factory workspace

You are the re:factory supervisor. Use /slash commands and factory CLI to manage projects.
See your system prompt for full instructions.
"""

SOP_COMPACT_DIR = Path(__file__).parent / "agents" / "sop-compact"


def setup_workspace(project_path: Path) -> Path:
    """Set up re:factory for a project.

    Session state goes in <project>/.refactory/. Skills and settings are
    installed into the PROJECT's .claude/ so the agent runs from the
    project root with full access to the source tree.

    Idempotent — safe to call on every launch. Overwrites settings and
    skills to pick up updates.

    Returns the workspace path (.refactory/).
    """
    workspace = project_path / ".refactory"
    workspace.mkdir(parents=True, exist_ok=True)

    sop_dir = workspace / ".claude" / "sop-compact"
    sop_dir.mkdir(parents=True, exist_ok=True)

    for hook_name in ("pre-compact.sh", "session-start.sh"):
        src = SOP_COMPACT_DIR / hook_name
        if src.is_file():
            dst = sop_dir / hook_name
            shutil.copy2(src, dst)
            dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    sop_src = SOP_COMPACT_DIR / "sop-compact.md"
    if sop_src.is_file():
        shutil.copy2(sop_src, workspace / ".claude" / "sop-compact.md")

    project_claude_dir = project_path / ".claude"
    project_claude_dir.mkdir(exist_ok=True)

    commands_dir = project_claude_dir / "commands"
    commands_dir.mkdir(exist_ok=True)

    skills_src = Path(__file__).parent / "agents" / "skills"
    if skills_src.is_dir():
        for skill_file in skills_src.glob("*.md"):
            shutil.copy2(skill_file, commands_dir / skill_file.name)

    settings = dict(SETTINGS_JSON)
    settings["hooks"] = {
        "PreCompact": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": str((project_path / ".refactory" / ".claude" / "sop-compact" / "pre-compact.sh").resolve()),
                    }
                ]
            }
        ],
        "SessionStart": [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": str((project_path / ".refactory" / ".claude" / "sop-compact" / "session-start.sh").resolve()),
                    }
                ],
            }
        ],
    }

    settings_path = project_claude_dir / "settings.local.json"
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    claude_md_path = workspace / "CLAUDE.md"
    claude_md_path.write_text(CLAUDE_MD_CONTENT)

    return workspace


def get_session_id(project_path: Path, reset: bool = False) -> str:
    """Read or create a persistent session ID for a project.

    The session ID is stored in <project>/.refactory/session.json.

    Args:
        project_path: Root directory of the project.
        reset: If True, generate a new session ID even if one exists.

    Returns:
        The session ID string.
    """
    session_file = project_path / ".refactory" / "session.json"
    if not reset and session_file.exists():
        try:
            data = json.loads(session_file.read_text())
            sid = data.get("session_id")
            if isinstance(sid, str) and sid:
                return sid
        except (json.JSONDecodeError, KeyError):
            pass

    sid = str(uuid.uuid4())
    save_session_id(project_path, sid)
    return sid


def save_session_id(project_path: Path, session_id: str) -> None:
    """Write session state to <project>/.refactory/session.json."""
    session_file = project_path / ".refactory" / "session.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "session_id": session_id,
        "created": datetime.now(timezone.utc).isoformat(),
    }
    session_file.write_text(json.dumps(data, indent=2) + "\n")
