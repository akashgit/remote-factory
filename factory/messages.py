"""User-to-CEO message channel.

Users queue messages via ``factory message``. The factory loop reads pending
messages and injects them into the CEO's task string each cycle.
"""

from __future__ import annotations

import shutil
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

    msg_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    ts = datetime.now(timezone.utc)

    path = msg_dir / f"{msg_id}.md"
    path.write_text(f"timestamp: {ts.isoformat()}\n\n{text}\n")

    msg = Message(id=msg_id, timestamp=ts, text=text)
    log.info("message_written", id=msg_id, chars=len(text))
    return msg


def read_pending(project_path: Path) -> list[Message]:
    """Read all pending (unread) messages, ordered by timestamp."""
    msg_dir = _messages_dir(project_path)
    if not msg_dir.exists():
        return []

    messages: list[Message] = []
    for path in sorted(msg_dir.glob("*.md")):
        if path.parent.name == "read":
            continue
        content = path.read_text()
        lines = content.split("\n", 2)
        ts = datetime.now(timezone.utc)
        if lines and lines[0].startswith("timestamp:"):
            try:
                ts = datetime.fromisoformat(lines[0].split(":", 1)[1].strip())
            except ValueError:
                pass
        text = lines[2].strip() if len(lines) > 2 else content.strip()
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
            shutil.move(str(src), str(read_dir / src.name))
            log.debug("message_marked_read", id=msg_id)
