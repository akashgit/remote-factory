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
    claude_session_id: str | None = None,
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
               (id, parent_id, root_id, kind, title, agent_role, claude_session_id,
                status, model, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)""",
            (session_id, parent_id, effective_root, kind, title, role,
             claude_session_id, model, now, now),
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
                claude_session_id = COALESCE(?, claude_session_id),
                model = COALESCE(?, model),
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

        effective_csid = claude_session_id
        if not effective_csid:
            row = conn.execute(
                "SELECT claude_session_id, agent_role, created_at FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row:
                effective_csid = row["claude_session_id"]
                if not effective_csid:
                    discovered = _discover_claude_session_id(
                        row["agent_role"] or "", row["created_at"], project_path,
                    )
                    if discovered:
                        conn.execute(
                            "UPDATE sessions SET claude_session_id = ? WHERE id = ?",
                            (discovered, session_id),
                        )
                        effective_csid = discovered

        ingested = False
        if effective_csid and isinstance(effective_csid, str):
            conn.execute(
                "DELETE FROM session_items WHERE session_id = ?", (session_id,),
            )
            ingested = _ingest_transcript(conn, session_id, effective_csid, project_path)

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


def _find_transcript(claude_session_id: str, project_path: Path) -> Path | None:
    """Locate a Claude Code transcript file, trying multiple path patterns.

    Tries the direct project path hash first, then scans all project dirs
    as a fallback (handles worktree vs project dir mismatches).
    """
    claude_dir = Path.home() / ".claude" / "projects"
    dir_name = str(project_path.resolve()).replace("/", "-").replace(".", "-")
    direct = claude_dir / dir_name / f"{claude_session_id}.jsonl"
    if direct.exists():
        return direct
    if claude_dir.exists():
        for pdir in claude_dir.iterdir():
            if pdir.is_dir():
                candidate = pdir / f"{claude_session_id}.jsonl"
                if candidate.exists():
                    return candidate
    return None


def _discover_claude_session_id(
    role: str,
    created_at: int,
    project_path: Path,
) -> str | None:
    """Find the claude_session_id for a running session by scanning transcripts.

    Looks for JSONL files in the Claude Code project directory that were
    modified after the session's created_at timestamp and contain a matching
    agent-name entry.
    """
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return None

    dir_name = str(project_path.resolve()).replace("/", "-").replace(".", "-")
    candidates: list[Path] = []

    search_dir = claude_dir / dir_name
    if search_dir.exists():
        for f in search_dir.iterdir():
            if not f.name.endswith(".jsonl") or f.is_dir():
                continue
            if f.stat().st_mtime >= created_at - 5:
                candidates.append(f)

    target_name = f"factory: {project_path.resolve().name}/{role}"

    matched_child_ids: set[str] = set()
    for f in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(f) as fh:
                for i, line in enumerate(fh):
                    if i > 5:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    item = json.loads(line)
                    if item.get("type") == "agent-name":
                        agent_name = item.get("agentName") or ""
                        if target_name in agent_name:
                            return f.stem
                        if "factory: " in agent_name and "/" in agent_name:
                            matched_child_ids.add(f.stem)
                    elif item.get("type") == "custom-title":
                        custom_title = item.get("customTitle") or ""
                        if target_name in custom_title:
                            return f.stem
                        if "factory: " in custom_title and "/" in custom_title:
                            matched_child_ids.add(f.stem)
        except Exception:
            continue

    if role == "ceo" and candidates:
        now = time.time()
        non_child = [f for f in candidates if f.stem not in matched_child_ids]
        if non_child:
            return min(non_child, key=lambda f: abs(f.stat().st_mtime - now)).stem

    return None


def _ingest_transcript(
    conn: sqlite3.Connection,
    session_id: str,
    claude_session_id: str,
    project_path: Path,
) -> bool:
    """Read Claude Code's conversation transcript and parse into session_items.

    Returns True if items were ingested, False if transcript was not found.
    """
    transcript_file = _find_transcript(claude_session_id, project_path)
    if transcript_file is None:
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

                tool_results: list[dict] = []
                text_parts: list[str] = []

                for part in content_parts:
                    if isinstance(part, str):
                        text_parts.append(part)
                    elif isinstance(part, dict):
                        if part.get("type") == "tool_result":
                            raw_content = part.get("content", [])
                            if isinstance(raw_content, list):
                                full_text = "".join(str(c) for c in raw_content)
                            else:
                                full_text = str(raw_content)
                            tool_results.append({
                                "tool_use_id": part.get("tool_use_id", ""),
                                "content": full_text,
                                "is_error": part.get("is_error", False),
                            })
                        elif part.get("type") == "text":
                            text_parts.append(part.get("text", ""))

                if tool_results:
                    for tr in tool_results:
                        content = tr["content"]
                        if not content.strip():
                            continue
                        data = json.dumps(tr)
                        _insert_item(
                            conn, session_id, position, "tool_output", "tool",
                            data, preview=content[:150],
                        )
                        position += 1
                elif text_parts:
                    text = "".join(text_parts)
                    if text.strip():
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
                        data = json.dumps({"name": tool_name, "input": tool_input})
                        input_str = json.dumps(tool_input)
                        preview = f"{tool_name}({input_str[:100]})"
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
    """Re-ingest transcripts for all sessions with a claude_session_id.

    Deletes existing items and re-parses from the JSONL transcript.
    Returns the number of sessions backfilled.
    """
    if not _db_path(project_path).exists():
        return 0

    conn = _connect(project_path)
    try:
        rows = conn.execute(
            """SELECT s.id, s.claude_session_id
               FROM sessions s
               WHERE s.claude_session_id IS NOT NULL"""
        ).fetchall()

        count = 0
        for row in rows:
            sid = row["id"]
            claude_sid = row["claude_session_id"]
            if _find_transcript(claude_sid, project_path) is None:
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


def reingest_session(
    project_path: Path,
    session_id: str,
    *,
    usage_update: dict | None = None,
) -> dict | None:
    """Delete existing items and re-parse the transcript for a session.

    If *usage_update* is provided, it should be a dict with keys like
    ``input_tokens``, ``output_tokens``, ``cache_read_tokens``,
    ``cost_usd``, ``num_turns`` — these are added to the session's
    existing totals.

    Returns the updated session dict, or None if the session has no claude_session_id.
    """
    if not _db_path(project_path).exists():
        return None

    conn = _connect(project_path)
    try:
        row = conn.execute(
            "SELECT claude_session_id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row or not row["claude_session_id"]:
            return None

        if usage_update:
            now = int(time.time())
            conn.execute(
                """UPDATE sessions SET
                    input_tokens = input_tokens + ?,
                    output_tokens = output_tokens + ?,
                    cache_read_tokens = cache_read_tokens + ?,
                    total_cost_usd = total_cost_usd + ?,
                    num_turns = num_turns + ?,
                    updated_at = ?
                   WHERE id = ?""",
                (
                    usage_update.get("input_tokens", 0),
                    usage_update.get("output_tokens", 0),
                    usage_update.get("cache_read_tokens", 0),
                    usage_update.get("cost_usd", 0.0),
                    usage_update.get("num_turns", 0),
                    now,
                    session_id,
                ),
            )

        conn.execute("DELETE FROM session_items WHERE session_id = ?", (session_id,))
        _ingest_transcript(conn, session_id, row["claude_session_id"], project_path)
        conn.commit()
    finally:
        conn.close()
    return get_session(project_path, session_id)


def get_cycles(
    project_path: Path,
    *,
    limit: int = 20,
) -> list[dict]:
    """List root sessions (CEO cycles) with aggregated stats from children."""
    if not _db_path(project_path).exists():
        return []

    conn = _connect(project_path)
    try:
        rows = conn.execute(
            """SELECT s.*,
                (SELECT COUNT(*) FROM sessions c WHERE c.parent_id = s.id) as child_count,
                s.total_cost_usd + COALESCE(
                    (SELECT SUM(c.total_cost_usd) FROM sessions c WHERE c.parent_id = s.id), 0
                ) as total_cost,
                s.duration_ms + COALESCE(
                    (SELECT SUM(c.duration_ms) FROM sessions c WHERE c.parent_id = s.id), 0
                ) as total_duration,
                (SELECT GROUP_CONCAT(DISTINCT c.agent_role)
                    FROM sessions c WHERE c.parent_id = s.id) as child_roles
            FROM sessions s
            WHERE s.kind = 'default' OR s.parent_id IS NULL
            ORDER BY s.created_at DESC
            LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_cycle(
    project_path: Path,
    cycle_id: str,
) -> dict | None:
    """Get a cycle with its root session and all children, plus aggregated stats."""
    root = get_session(project_path, cycle_id)
    if root is None:
        return None

    children = get_children(project_path, cycle_id)

    total_cost = (root.get("total_cost_usd") or 0.0)
    total_duration = (root.get("duration_ms") or 0.0)
    for child in children:
        total_cost += child.get("total_cost_usd") or 0.0
        total_duration += child.get("duration_ms") or 0.0

    return {
        **root,
        "children": children,
        "total_cost": total_cost,
        "total_duration": total_duration,
    }


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
    """Get a single session with its items.

    For running sessions with a claude_session_id, re-ingests the
    transcript on every read so the dashboard shows live progress.
    """
    if not _db_path(project_path).exists():
        return None

    conn = _connect(project_path)
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        result = dict(row)

        if result["status"] == "running":
            csid = result.get("claude_session_id")
            if not csid:
                csid = _discover_claude_session_id(
                    result.get("agent_role", ""),
                    result.get("created_at", 0),
                    project_path,
                )
                if csid:
                    conn.execute(
                        "UPDATE sessions SET claude_session_id = ? WHERE id = ?",
                        (csid, session_id),
                    )
                    result["claude_session_id"] = csid
            if csid:
                conn.execute(
                    "DELETE FROM session_items WHERE session_id = ?",
                    (session_id,),
                )
                _ingest_transcript(conn, session_id, csid, project_path)
                conn.commit()

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
