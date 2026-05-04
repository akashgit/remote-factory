"""Message channel — allows users to inject directives into a running CEO cycle.

Messages are written to .factory/messages/<timestamp>.md and read by the CEO
before each cycle. After injection, messages are moved to .factory/messages/read/.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import structlog

log = structlog.get_logger()

_MESSAGES_DIR = "messages"
_READ_DIR = "read"


@dataclass
class Message:
    """A single user message destined for the CEO."""

    id: str
    timestamp: datetime
    text: str


def _messages_dir(project_path: Path) -> Path:
    return project_path / ".factory" / _MESSAGES_DIR


def _read_dir(project_path: Path) -> Path:
    return _messages_dir(project_path) / _READ_DIR


def write_message(project_path: Path, text: str) -> Message:
    """Write a timestamped message file to .factory/messages/.

    Returns the created Message.
    """
    now = datetime.now()
    msg_id = now.strftime("%Y%m%dT%H%M%S_%f")
    msg_dir = _messages_dir(project_path)
    msg_dir.mkdir(parents=True, exist_ok=True)

    msg_path = msg_dir / f"{msg_id}.md"
    msg_path.write_text(text)

    msg = Message(id=msg_id, timestamp=now, text=text)
    log.info("message_written", id=msg_id, project=str(project_path))
    return msg


def read_pending(project_path: Path) -> list[Message]:
    """Read all unread messages from .factory/messages/ (excludes read/ subdir).

    Returns messages sorted by timestamp (oldest first).
    """
    msg_dir = _messages_dir(project_path)
    if not msg_dir.exists():
        return []

    messages: list[Message] = []
    for path in sorted(msg_dir.glob("*.md")):
        if not path.is_file():
            continue
        msg_id = path.stem
        try:
            # Parse timestamp from the ID format: YYYYMMDDTHHMMSS_ffffff
            ts = datetime.strptime(msg_id, "%Y%m%dT%H%M%S_%f")
        except ValueError:
            ts = datetime.fromtimestamp(path.stat().st_mtime)
        text = path.read_text()
        messages.append(Message(id=msg_id, timestamp=ts, text=text))

    log.debug("messages_read_pending", count=len(messages))
    return messages


def mark_read(project_path: Path, message_ids: list[str]) -> None:
    """Move processed messages to .factory/messages/read/.

    Silently skips IDs that don't correspond to existing message files.
    """
    if not message_ids:
        return

    msg_dir = _messages_dir(project_path)
    read = _read_dir(project_path)
    read.mkdir(parents=True, exist_ok=True)

    for msg_id in message_ids:
        src = msg_dir / f"{msg_id}.md"
        if src.exists():
            shutil.move(str(src), str(read / f"{msg_id}.md"))
            log.debug("message_marked_read", id=msg_id)
