"""Canonical paths for ACE playbook storage.

User-evolved playbooks go to ~/.factory/playbooks/<role>.md (or
FACTORY_PLAYBOOKS_DIR if set). Factory defaults stay in the source
tree and are read-only at runtime.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULTS_DIR: Path = Path(__file__).parent.parent / "agents" / "playbooks"


def user_playbooks_dir() -> Path:
    override = os.environ.get("FACTORY_PLAYBOOKS_DIR")
    if override:
        d = Path(override).expanduser().resolve()
    else:
        d = Path.home() / ".factory" / "playbooks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_playbook_path(role: str) -> Path | None:
    """Return the highest-priority playbook path that exists.

    Order: user-local evolved > factory default.
    Project-specific overrides are handled separately by runner.py.
    """
    user_path = user_playbooks_dir() / f"{role}.md"
    if user_path.exists():
        return user_path
    default_path = DEFAULTS_DIR / f"{role}.md"
    if default_path.exists():
        return default_path
    return None


def user_playbook_path(role: str) -> Path:
    """Return the user-local playbook path (for ACE writes)."""
    return user_playbooks_dir() / f"{role}.md"


def seed_user_playbooks() -> None:
    """Copy factory defaults into user-local dir for roles that have no
    user-local playbook yet. Ensures counter updates have a file to operate on."""
    dest = user_playbooks_dir()
    for default in sorted(DEFAULTS_DIR.glob("*.md")):
        user_file = dest / default.name
        if not user_file.exists():
            user_file.write_text(default.read_text())
