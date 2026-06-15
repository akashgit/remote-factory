"""AGENTS.md setup/restore helpers for system prompt isolation.

Codex and OpenCode read AGENTS.md as system-level instructions. These helpers
write the factory prompt into AGENTS.md (backing up any existing content) and
restore it in a finally block, guarded by a file lock to prevent races.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog
from filelock import FileLock

log = structlog.get_logger()

SENTINEL = "<!-- factory:system-prompt -->"


@dataclass
class AgentsMdState:
    """Tracks the AGENTS.md file state for later restoration."""

    path: Path
    lock: FileLock = field(repr=False)
    backup: str | None = None


def setup_agents_md(cwd: Path, prompt: str) -> AgentsMdState:
    """Write the factory prompt into AGENTS.md, backing up existing content.

    If an existing AGENTS.md starts with SENTINEL, it's stale from a prior
    crashed run — discard it (no backup). Otherwise back up and prepend.
    """
    agents_path = cwd / "AGENTS.md"
    lock_path = cwd / ".factory" / ".agents_md.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(lock_path)
    lock.acquire()

    backup: str | None = None
    content = f"{SENTINEL}\n{prompt}\n"

    if agents_path.is_file():
        existing = agents_path.read_text(encoding="utf-8")
        if existing.startswith(SENTINEL):
            log.debug("agents_md_stale_discarded", path=str(agents_path))
        else:
            backup = existing
            content = f"{backup}\n{SENTINEL}\n{prompt}\n"

    agents_path.write_text(content, encoding="utf-8")
    log.debug("agents_md_written", path=str(agents_path), has_backup=backup is not None)

    return AgentsMdState(path=agents_path, backup=backup, lock=lock)


def restore_agents_md(state: AgentsMdState | None) -> None:
    """Restore AGENTS.md to its pre-setup state and release the lock."""
    if state is None:
        return
    try:
        if state.backup is not None:
            state.path.write_text(state.backup, encoding="utf-8")
            log.debug("agents_md_restored", path=str(state.path))
        else:
            state.path.unlink(missing_ok=True)
            log.debug("agents_md_removed", path=str(state.path))
    except OSError:
        log.debug("agents_md_restore_failed", path=str(state.path), exc_info=True)
    finally:
        state.lock.release()
