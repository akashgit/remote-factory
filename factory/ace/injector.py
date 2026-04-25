"""Injector — load and inject playbooks into agent prompts.

Playbooks are resolved with a two-tier lookup:
  1. User-local evolved: ~/.factory/playbooks/<role>.md
  2. Factory defaults:   factory/agents/playbooks/<role>.md
"""

from __future__ import annotations

import structlog

from factory.ace.paths import resolve_playbook_path

log = structlog.get_logger()


def load_playbook(role: str) -> str | None:
    """Load the playbook for an agent role, if it exists.

    Returns the playbook content as a string, or None if no playbook exists.
    """
    path = resolve_playbook_path(role)
    if path is None:
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
