"""Canonical paths for ACE playbook storage.

User-evolved playbooks go to ~/.factory/playbooks/<role>.md (or
FACTORY_PLAYBOOKS_DIR if set). Factory defaults stay in the source
tree and are read-only at runtime.
"""

from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger()

DEFAULTS_DIR: Path = Path(__file__).parent.parent / "agents" / "playbooks"


def user_playbooks_dir() -> Path:
    from factory.user_config import resolve

    override = resolve("playbooks_dir", env_var="FACTORY_PLAYBOOKS_DIR")
    if override:
        d = Path(override).expanduser().resolve()
        log.debug("user_playbooks_dir.override", path=str(d))
    else:
        d = Path.home() / ".factory" / "playbooks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_playbook_path(role: str) -> Path | None:
    """Return the highest-priority playbook path that exists.

    Order: user-local evolved > factory default.
    Project-specific overrides are handled separately by runner.py.
    """
    log.debug("resolve_playbook_path.start", role=role)
    user_path = user_playbooks_dir() / f"{role}.md"
    if user_path.exists():
        log.debug("resolve_playbook_path.found", source="user", path=str(user_path))
        return user_path
    default_path = DEFAULTS_DIR / f"{role}.md"
    if default_path.exists():
        log.debug("resolve_playbook_path.found", source="default", path=str(default_path))
        return default_path
    log.debug("resolve_playbook_path.not_found", role=role)
    return None


def user_playbook_path(role: str) -> Path:
    """Return the user-local playbook path (for ACE writes)."""
    return user_playbooks_dir() / f"{role}.md"


def seed_user_playbooks() -> None:
    """Copy factory defaults into user-local dir for roles that have no
    user-local playbook yet. Ensures counter updates have a file to operate on."""
    log.info("seed_user_playbooks.start")
    dest = user_playbooks_dir()
    seeded = 0
    for default in sorted(DEFAULTS_DIR.glob("*.md")):
        user_file = dest / default.name
        if not user_file.exists():
            user_file.write_text(default.read_text())
            seeded += 1
    log.info("seed_user_playbooks.done", seeded=seeded)
