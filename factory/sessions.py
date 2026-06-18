"""Session persistence — SQLite capture layer for agent invocations."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import structlog

log = structlog.get_logger()

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    parent_id       TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    root_id         TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'default' CHECK(kind IN ('default','sub_agent')),
    title           TEXT,
    agent_role      TEXT,
    claude_session_id TEXT,
    status          TEXT NOT NULL DEFAULT 'running',
    stop_reason     TEXT,
    terminal_reason TEXT,
    model           TEXT,
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    total_cost_usd  REAL DEFAULT 0.0,
    duration_ms     REAL DEFAULT 0.0,
    num_turns       INTEGER DEFAULT 0,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_parent
    ON sessions(parent_id, created_at DESC) WHERE kind = 'sub_agent';
CREATE INDEX IF NOT EXISTS idx_sessions_root ON sessions(root_id);
CREATE INDEX IF NOT EXISTS idx_sessions_role ON sessions(agent_role);

CREATE TABLE IF NOT EXISTS session_items (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    type            TEXT NOT NULL,
    role            TEXT,
    data            TEXT NOT NULL,
    preview         TEXT,
    created_at      INTEGER NOT NULL,
    UNIQUE(session_id, position)
);
"""


def _generate_id(prefix: str = "sess") -> str:
    return f"{prefix}_{os.urandom(4).hex()}"


def _db_path(project_path: Path) -> Path:
    return project_path / ".factory" / "sessions.db"


def _connect(project_path: Path) -> sqlite3.Connection:
    path = _db_path(project_path)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(project_path: Path) -> Path:
    """Create the sessions database and tables. Returns the db file path."""
    db = _db_path(project_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(project_path)
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
    log.debug("sessions_db_initialized", path=str(db))
    return db


def begin_session(
    project_path: Path,
    role: str,
    *,
    parent_id: str | None = None,
    root_id: str | None = None,
    title: str | None = None,
    model: str | None = None,
) -> str:
    """Insert a new session row and return its ID."""
    init_db(project_path)
    session_id = _generate_id()
    now = int(time.time())
    kind = "sub_agent" if parent_id else "default"

    conn = _connect(project_path)
    try:
        if parent_id and not root_id:
            parent_row = conn.execute(
                "SELECT root_id FROM sessions WHERE id = ?", (parent_id,)
            ).fetchone()
            effective_root = parent_row["root_id"] if parent_row else session_id
        else:
            effective_root = root_id or session_id

        conn.execute(
            """INSERT INTO sessions
               (id, parent_id, root_id, kind, title, agent_role, status, model, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)""",
            (session_id, parent_id, effective_root, kind, title, role, model, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    log.debug("session_started", session_id=session_id, role=role, parent_id=parent_id)
    return session_id


def complete_session(
    project_path: Path,
    session_id: str,
    *,
    status: str = "completed",
    usage: object | None = None,
    metadata: dict[str, object] | None = None,
    output: str | None = None,
) -> None:
    """Update a session with completion data."""
    now = int(time.time())
    meta = metadata or {}

    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    total_cost_usd = 0.0
    duration_ms = 0.0
    num_turns = 0
    model: str | None = None

    if usage is not None:
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_read_tokens = getattr(usage, "cache_read_tokens", 0) or 0
        total_cost_usd = getattr(usage, "total_cost_usd", 0.0) or 0.0
        duration_ms = getattr(usage, "duration_ms", 0.0) or 0.0
        num_turns = getattr(usage, "num_turns", 0) or 0
        model = getattr(usage, "model", None)

    stop_reason = meta.get("stop_reason")
    terminal_reason = meta.get("terminal_reason")
    claude_session_id = meta.get("session_id")

    conn = _connect(project_path)
    try:
        conn.execute(
            """UPDATE sessions SET
                status = ?, stop_reason = ?, terminal_reason = ?,
                claude_session_id = ?, model = COALESCE(?, model),
                input_tokens = ?, output_tokens = ?, cache_read_tokens = ?,
                total_cost_usd = ?, duration_ms = ?, num_turns = ?,
                updated_at = ?
               WHERE id = ?""",
            (
                status, stop_reason, terminal_reason,
                claude_session_id, model,
                input_tokens, output_tokens, cache_read_tokens,
                total_cost_usd, duration_ms, num_turns,
                now, session_id,
            ),
        )
        ingested = False
        if claude_session_id and isinstance(claude_session_id, str):
            ingested = _ingest_transcript(conn, session_id, claude_session_id, project_path)

        if not ingested and output:
            item_id = _generate_id("item")
            conn.execute(
                """INSERT INTO session_items
                   (id, session_id, position, type, role, data, preview, created_at)
                   VALUES (?, ?, 0, 'message', 'assistant', ?, ?, ?)""",
                (item_id, session_id, output, output[:200] if output else None, now),
            )
        conn.commit()
    finally:
        conn.close()

    log.debug("session_completed", session_id=session_id, status=status)


def _ingest_transcript(
    conn: sqlite3.Connection,
    session_id: str,
    claude_session_id: str,
    project_path: Path,
) -> bool:
    """Read Claude Code's conversation transcript and parse into session_items.

    Returns True if items were ingested, False if transcript was not found.
    """
    claude_dir = Path.home() / ".claude" / "projects"
    project_str = str(project_path.resolve())
    dir_name = project_str.replace("/", "-")
    if dir_name.startswith("-"):
        dir_name = dir_name  # keep leading dash
    transcript_file = claude_dir / dir_name / f"{claude_session_id}.jsonl"

    if not transcript_file.exists():
        return False

    position = 0
    with open(transcript_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            item_type = item.get("type", "")

            if item_type == "user":
                msg = item.get("message", {})
                content_parts = msg.get("content", [])
                text = ""
                for part in content_parts:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            text += part.get("text", "")
                        elif part.get("type") == "tool_result":
                            text += f'[Tool Result: {part.get("tool_use_id", "")[:12]}...]'
                if not text:
                    continue
                _insert_item(conn, session_id, position, "message", "user", text)
                position += 1

            elif item_type == "assistant":
                msg = item.get("message", {})
                content = msg.get("content", [])
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    ptype = part.get("type", "")
                    if ptype == "text":
                        text = part.get("text", "")
                        if text.strip():
                            _insert_item(conn, session_id, position, "message", "assistant", text)
                            position += 1
                    elif ptype == "tool_use":
                        tool_name = part.get("name", "unknown")
                        tool_input = part.get("input", {})
                        data = json.dumps({"name": tool_name, "input": tool_input}, indent=2)
                        input_str = json.dumps(tool_input)
                        preview = f"{tool_name}({input_str[:100]}...)"
                        _insert_item(
                            conn, session_id, position, "tool_call", "assistant",
                            data, preview=preview,
                        )
                        position += 1
                    elif ptype == "thinking":
                        text = part.get("thinking", "")
                        if text.strip():
                            _insert_item(
                                conn, session_id, position, "thinking", "assistant",
                                text, preview=text[:150],
                            )
                            position += 1

            elif item_type == "tool_result":
                content = item.get("content", [])
                text = ""
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text += part.get("text", "")
                    elif isinstance(part, str):
                        text += part
                if text.strip():
                    _insert_item(
                        conn, session_id, position, "tool_output", "tool",
                        text, preview=text[:150],
                    )
                    position += 1

    return position > 0


def _insert_item(
    conn: sqlite3.Connection,
    session_id: str,
    position: int,
    item_type: str,
    role: str,
    data: str,
    *,
    preview: str | None = None,
) -> None:
    item_id = _generate_id("item")
    now = int(time.time())
    if preview is None:
        preview = data[:200] if data else None
    conn.execute(
        """INSERT OR IGNORE INTO session_items
           (id, session_id, position, type, role, data, preview, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (item_id, session_id, position, item_type, role, data, preview, now),
    )


def backfill_transcripts(project_path: Path) -> int:
    """Re-ingest transcripts for sessions that only have the old single-blob item.

    Returns the number of sessions backfilled.
    """
    if not _db_path(project_path).exists():
        return 0

    conn = _connect(project_path)
    try:
        rows = conn.execute(
            """SELECT s.id, s.claude_session_id
               FROM sessions s
               WHERE s.claude_session_id IS NOT NULL
                 AND (SELECT COUNT(*) FROM session_items si WHERE si.session_id = s.id) = 1"""
        ).fetchall()

        count = 0
        for row in rows:
            sid = row["id"]
            claude_sid = row["claude_session_id"]
            # Check if transcript exists before deleting old items
            claude_dir = Path.home() / ".claude" / "projects"
            project_str = str(project_path.resolve())
            dir_name = project_str.replace("/", "-")
            transcript_file = claude_dir / dir_name / f"{claude_sid}.jsonl"
            if not transcript_file.exists():
                continue
            conn.execute(
                "DELETE FROM session_items WHERE session_id = ?", (sid,),
            )
            ingested = _ingest_transcript(conn, sid, claude_sid, project_path)
            if ingested:
                count += 1

        conn.commit()
        return count
    finally:
        conn.close()


def get_sessions(
    project_path: Path,
    *,
    cycle_id: str | None = None,
    role: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List sessions with child_count, optionally filtered by root_id (cycle) or role."""
    if not _db_path(project_path).exists():
        return []

    conditions: list[str] = []
    params: list[object] = []

    if cycle_id:
        conditions.append("s.root_id = ?")
        params.append(cycle_id)
    if role:
        conditions.append("s.agent_role = ?")
        params.append(role)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    conn = _connect(project_path)
    try:
        rows = conn.execute(
            f"""SELECT s.*,
                       (SELECT COUNT(*) FROM sessions c WHERE c.parent_id = s.id) AS child_count
                FROM sessions s {where}
                ORDER BY s.created_at DESC LIMIT ?""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_session(project_path: Path, session_id: str) -> dict | None:
    """Get a single session with its items."""
    if not _db_path(project_path).exists():
        return None

    conn = _connect(project_path)
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        items = conn.execute(
            "SELECT * FROM session_items WHERE session_id = ? ORDER BY position",
            (session_id,),
        ).fetchall()
        result["items"] = [dict(i) for i in items]
        return result
    finally:
        conn.close()


def get_children(project_path: Path, session_id: str) -> list[dict]:
    """Get child sessions with child_count and last message preview."""
    if not _db_path(project_path).exists():
        return []

    conn = _connect(project_path)
    try:
        rows = conn.execute(
            """SELECT s.*,
                      (SELECT COUNT(*) FROM sessions c WHERE c.parent_id = s.id) AS child_count,
                      (SELECT si.preview FROM session_items si
                       WHERE si.session_id = s.id ORDER BY si.position DESC LIMIT 1) AS last_message_preview
               FROM sessions s
               WHERE s.parent_id = ?
               ORDER BY s.created_at""",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
