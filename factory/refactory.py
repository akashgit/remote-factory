"""Workspace setup and session management for the re:factory agent."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_DIR = Path.home() / ".factory" / "refactory"
SESSION_FILE = Path.home() / ".factory" / "refactory-session.json"

SETTINGS_JSON = {
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


def setup_workspace() -> Path:
    """Create the re:factory workspace at ~/.factory/refactory/.

    Idempotent — safe to call on every launch. Creates directory structure
    and writes config files, overwriting settings.json and CLAUDE.md to
    pick up any updates.

    Returns the workspace path.
    """
    workspace = WORKSPACE_DIR
    workspace.mkdir(parents=True, exist_ok=True)

    claude_dir = workspace / ".claude"
    claude_dir.mkdir(exist_ok=True)

    commands_dir = claude_dir / "commands"
    commands_dir.mkdir(exist_ok=True)

    settings_path = claude_dir / "settings.json"
    settings_path.write_text(json.dumps(SETTINGS_JSON, indent=2) + "\n")

    claude_md_path = workspace / "CLAUDE.md"
    claude_md_path.write_text(CLAUDE_MD_CONTENT)

    skills_src = Path(__file__).parent / "agents" / "skills"
    if skills_src.is_dir():
        for skill_file in skills_src.glob("*.md"):
            shutil.copy2(skill_file, commands_dir / skill_file.name)

    return workspace


def get_session_id(reset: bool = False) -> str:
    """Read or create a persistent session ID.

    The session ID is stored in ~/.factory/refactory-session.json (outside
    the workspace, so it survives workspace regeneration).

    Args:
        reset: If True, generate a new session ID even if one exists.

    Returns:
        The session ID string.
    """
    if not reset and SESSION_FILE.exists():
        try:
            data = json.loads(SESSION_FILE.read_text())
            sid = data.get("session_id")
            if isinstance(sid, str) and sid:
                return sid
        except (json.JSONDecodeError, KeyError):
            pass

    sid = str(uuid.uuid4())
    save_session_id(sid)
    return sid


def save_session_id(session_id: str) -> None:
    """Write session state to ~/.factory/refactory-session.json."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "session_id": session_id,
        "created": datetime.now(timezone.utc).isoformat(),
    }
    SESSION_FILE.write_text(json.dumps(data, indent=2) + "\n")
