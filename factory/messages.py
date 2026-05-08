"""User-to-CEO message channel.

Users queue messages via ``factory message``. The factory loop reads pending
messages and injects them into the CEO's task string each cycle.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import structlog

log = structlog.get_logger()


@dataclass
class Message:
    id: str
    timestamp: datetime
    text: str


def _messages_dir(project_path: Path) -> Path:
    return project_path / ".factory" / "messages"


def _read_dir(project_path: Path) -> Path:
    return _messages_dir(project_path) / "read"


def write_message(project_path: Path, text: str) -> Message:
    """Write a message to the pending queue. Returns the created Message."""
    msg_dir = _messages_dir(project_path)
    msg_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc)
    msg_id = ts.strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex[:8]

    path = msg_dir / f"{msg_id}.md"
    path.write_text(f"timestamp: {ts.isoformat()}\n\n{text}\n")

    msg = Message(id=msg_id, timestamp=ts, text=text)
    log.info("message_written", id=msg_id, chars=len(text))
    return msg


MAX_PENDING_MESSAGES = 20
MAX_TOTAL_CHARS = 50_000


def read_pending(
    project_path: Path,
    max_messages: int = MAX_PENDING_MESSAGES,
    max_chars: int = MAX_TOTAL_CHARS,
) -> list[Message]:
    """Read pending (unread) messages, ordered by timestamp.

    Caps at ``max_messages`` messages or ``max_chars`` total characters
    to prevent flooding the CEO task string.
    """
    msg_dir = _messages_dir(project_path)
    if not msg_dir.exists():
        return []

    all_paths = sorted(msg_dir.glob("*.md"))
    if len(all_paths) > max_messages:
        log.warning("messages_capped", total=len(all_paths), cap=max_messages)

    messages: list[Message] = []
    total_chars = 0
    for path in all_paths:
        if len(messages) >= max_messages:
            break
        content = path.read_text()
        lines = content.split("\n", 2)
        ts = datetime.now(timezone.utc)
        if lines and lines[0].startswith("timestamp:"):
            try:
                ts = datetime.fromisoformat(lines[0].split(":", 1)[1].strip())
            except ValueError:
                pass
        text = lines[2].strip() if len(lines) > 2 else content.strip()
        if total_chars + len(text) > max_chars and messages:
            log.warning("messages_chars_capped", total_chars=total_chars + len(text), cap=max_chars)
            break
        total_chars += len(text)
        messages.append(Message(id=path.stem, timestamp=ts, text=text))

    return messages


def mark_read(project_path: Path, message_ids: list[str]) -> None:
    """Move messages to the read/ subdirectory."""
    msg_dir = _messages_dir(project_path)
    read_dir = _read_dir(project_path)
    read_dir.mkdir(parents=True, exist_ok=True)

    for msg_id in message_ids:
        src = msg_dir / f"{msg_id}.md"
        if src.exists():
            src.rename(read_dir / src.name)
            log.debug("message_marked_read", id=msg_id)
