"""Injector — load and inject playbooks into agent prompts.

Playbooks are stored at factory/agents/playbooks/<role>.md and are
auto-appended to agent prompts at invocation time.
"""

from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger()

# Directory containing evolved playbooks (shipped with the factory)
_PLAYBOOKS_DIR = Path(__file__).parent.parent / "agents" / "playbooks"


def load_playbook(role: str) -> str | None:
    """Load the playbook for an agent role, if it exists.

    Returns the playbook content as a string, or None if no playbook exists.
    """
    path = _PLAYBOOKS_DIR / f"{role}.md"
    if not path.exists():
        return None
    content = path.read_text().strip()
    if not content:
        return None
    log.debug("playbook_loaded", role=role, path=str(path))
    return content


def inject_playbook(prompt: str, playbook: str) -> str:
    """Inject a playbook section into an agent prompt.

    Inserts the playbook at the end of the base prompt, before any
    task-specific content that may be appended later.
    """
    return (
        f"{prompt}\n\n"
        f"---\n\n"
        f"## Behavioral Playbook (auto-evolved from experiment data)\n\n"
        f"Follow these empirically-derived rules. Items with higher helpful counts "
        f"are more strongly supported by data.\n\n"
        f"{playbook}"
    )
