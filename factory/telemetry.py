"""Langfuse tracing wrapper — graceful no-op when not configured."""

from __future__ import annotations

import json
import os
import threading
import time as _time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog
from langfuse import Langfuse
from langfuse.types import TraceContext

log = structlog.get_logger()

_HAS_LANGFUSE = True

_client: object | None = None
_observations: dict[str, Any] = {}
_trace_names: dict[str, tuple[str, Any]] = {}  # trace_id -> (name, input)

def is_enabled() -> bool:
    """Check if Langfuse is configured and lazily initialise the client."""
    global _client
    if _client is not None:
        return True
    if not _HAS_LANGFUSE:
        return False
    if not os.environ.get("LANGFUSE_HOST"):
        return False
    try:
        _client = Langfuse()
        log.debug("langfuse_initialized", host=os.environ["LANGFUSE_HOST"])
        return True
    except Exception as exc:
        log.warning("langfuse_init_failed", error=str(exc))
        return False


def _get_client() -> Any:
    if _client is None:
        raise RuntimeError("Langfuse not initialised — call is_enabled() first")
    return _client


_trace_name_counter: int = 0


def _update_trace_via_api(
    trace_id: str,
    name: str,
    input_data: object | None = None,
) -> None:
    """Set trace name and input via the Langfuse ingestion API.

    The v4 Python SDK derives trace names from observations, so we use
    the public ingestion batch endpoint to override it. Each call uses
    a unique event ID to avoid deduplication.
    """
    global _trace_name_counter
    import urllib.request
    from datetime import datetime, timezone

    host = os.environ.get("LANGFUSE_HOST", "")
    pub_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    sec_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not host or not pub_key:
        return
    try:
        import base64
        _trace_name_counter += 1
        auth = base64.b64encode(f"{pub_key}:{sec_key}".encode()).decode()
        inner: dict[str, Any] = {"id": trace_id, "name": name}
        if input_data is not None:
            inner["input"] = input_data
        body = {
            "batch": [{
                "id": f"trace-name-{trace_id[:8]}-{_trace_name_counter}",
                "type": "trace-create",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "body": inner,
            }],
        }
        req = urllib.request.Request(
            f"{host}/api/public/ingestion",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        log.debug("langfuse_trace_update_failed", trace_id=trace_id, exc_info=True)


def begin_trace(
    project_name: str,
    cycle_id: str | None = None,
    model: str | None = None,
) -> tuple[str, str] | None:
    """Create a root trace span. Returns (trace_id, span_id) or None."""
    if not is_enabled():
        return None
    client = _get_client()
    trace_name = f"factory:{project_name}/{cycle_id or 'cycle'}"
    trace_input = {"project": project_name, "cycle_id": cycle_id}
    obs = client.start_observation(
        name=trace_name,
        as_type="span",
        input=trace_input,
        metadata={"model": model, "project": project_name},
    )
    _observations[obs.id] = obs
    _trace_names[obs.trace_id] = (trace_name, trace_input)
    _update_trace_via_api(obs.trace_id, trace_name, trace_input)
    log.debug("langfuse_trace_started", trace_id=obs.trace_id, span_id=obs.id)
    return (obs.trace_id, obs.id)


def begin_span(
    trace_id: str,
    parent_span_id: str | None,
    role: str,
    model: str | None = None,
    task: str | None = None,
) -> str | None:
    """Create a child span. Uses TraceContext for cross-process linking."""
    if not is_enabled():
        return None
    client = _get_client()

    span_input = task
    parent = _observations.get(parent_span_id) if parent_span_id else None
    if parent is not None:
        obs = parent.start_observation(
            name=f"agent:{role}",
            as_type="span",
            input=span_input,
            metadata={"role": role, "model": model},
        )
    elif trace_id:
        tc: TraceContext = {"trace_id": trace_id}
        if parent_span_id:
            tc["parent_span_id"] = parent_span_id
        obs = client.start_observation(
            trace_context=tc,
            name=f"agent:{role}",
            as_type="span",
            input=span_input,
            metadata={"role": role, "model": model},
        )
    else:
        obs = client.start_observation(
            name=f"agent:{role}",
            as_type="span",
            input=span_input,
            metadata={"role": role, "model": model},
        )

    _observations[obs.id] = obs
    log.debug("langfuse_span_started", span_id=obs.id, role=role, trace_id=obs.trace_id)
    return obs.id


def end_span(
    trace_id: str,
    span_id: str,
    *,
    status: str = "completed",
    usage: object | None = None,
    metadata: dict[str, object] | None = None,
    output: str | None = None,
) -> None:
    """End a span, recording usage and metadata."""
    if not is_enabled() or not span_id:
        return
    obs = _observations.get(span_id)
    if obs is None:
        return

    meta = dict(metadata or {})
    meta["status"] = status

    if usage is not None:
        _g = usage.get if isinstance(usage, dict) else lambda k, d=None: getattr(usage, k, d)
        meta["input_tokens"] = _g("input_tokens", 0) or 0
        meta["output_tokens"] = _g("output_tokens", 0) or 0
        meta["cache_read_tokens"] = _g("cache_read_tokens", 0) or 0
        meta["total_cost_usd"] = _g("total_cost_usd", 0.0) or 0.0
        meta["duration_ms"] = _g("duration_ms", 0.0) or 0.0
        meta["num_turns"] = _g("num_turns", 0) or 0
        meta["model"] = _g("model", None)

    obs.update(output=output, metadata=meta)
    obs.end()
    _observations.pop(span_id, None)
    log.debug("langfuse_span_ended", span_id=span_id, status=status)


def end_trace(trace_id: str, span_id: str | None = None, output: str | None = None) -> None:
    """Mark a root trace span as finished and re-assert trace name."""
    if not is_enabled():
        return
    sid = span_id or trace_id
    obs = _observations.get(sid)
    if obs is not None:
        obs.update(output=output or {"status": "completed"})
        obs.end()
        _observations.pop(sid, None)
    saved = _trace_names.pop(trace_id, None)
    if saved:
        name, input_data = saved
        _update_trace_via_api(trace_id, name, input_data)
    log.debug("langfuse_trace_ended", trace_id=trace_id)


def flush() -> None:
    """Flush any buffered Langfuse events and re-assert trace names.

    The SDK derives trace names from observations, so we flush twice:
    first to drain the SDK's queue, then reassert names via the API,
    then flush again to ensure our name update is the final write.
    """
    if _client is not None:
        client = _get_client()
        client.flush()
        _time.sleep(1.0)
        for trace_id, (name, input_data) in list(_trace_names.items()):
            _update_trace_via_api(trace_id, name, input_data)
        client.flush()
        _time.sleep(0.3)


# ---------------------------------------------------------------------------
# Transcript ingestion
# ---------------------------------------------------------------------------


def _find_transcript(claude_session_id: str, project_path: Path) -> Path | None:
    """Locate a Claude Code transcript JSONL, trying multiple path patterns."""
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


def _process_transcript_item(
    item: dict,
    parent: Any,
    pending_tools: dict[str, Any],
) -> int:
    """Process a single JSONL transcript item into Langfuse observations.

    Mutates *pending_tools* in-place for tool call/result pairing.
    Returns the number of observations created.
    """
    count = 0
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
                    tool_use_id = part.get("tool_use_id", "")
                    raw = part.get("content", [])
                    text = (
                        "".join(str(c) for c in raw)
                        if isinstance(raw, list)
                        else str(raw)
                    )
                    tool_results.append({
                        "tool_use_id": tool_use_id,
                        "content": text,
                        "is_error": part.get("is_error", False),
                    })
                elif part.get("type") == "text":
                    text_parts.append(part.get("text", ""))

        for tr in tool_results:
            tool_use_id = tr["tool_use_id"]
            if tool_use_id in pending_tools:
                tool_obs = pending_tools.pop(tool_use_id)
                tool_obs.update(output=tr["content"])
                tool_obs.end()
                count += 1
            else:
                parent.create_event(
                    name="tool_output",
                    output=tr["content"],
                    metadata={"tool_use_id": tool_use_id},
                )
                count += 1

        if not tool_results and text_parts:
            text = "".join(text_parts)
            if text.strip():
                parent.create_event(
                    name="user_message",
                    input=text,
                )
                count += 1

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
                    parent.create_event(
                        name="assistant_message",
                        output=text,
                    )
                    count += 1
            elif ptype == "tool_use":
                tool_name = part.get("name", "unknown")
                tool_input = part.get("input", {})
                tool_use_id = part.get("id", "")
                tool_obs = parent.start_observation(
                    name=f"tool:{tool_name}",
                    as_type="tool",
                    input=tool_input,
                )
                if tool_use_id:
                    pending_tools[tool_use_id] = tool_obs
                else:
                    tool_obs.end()
                count += 1
            elif ptype == "thinking":
                text = part.get("thinking", "")
                if text.strip():
                    parent.create_event(
                        name="thinking",
                        output=text,
                    )
                    count += 1

    elif item_type == "tool_result":
        content = item.get("content", [])
        tool_use_id = item.get("tool_use_id", "")
        text = ""
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text += part.get("text", "")
            elif isinstance(part, str):
                text += part
        if text.strip():
            if tool_use_id and tool_use_id in pending_tools:
                tool_obs = pending_tools.pop(tool_use_id)
                tool_obs.update(output=text)
                tool_obs.end()
            else:
                parent.create_event(
                    name="tool_output",
                    output=text,
                )
            count += 1

    return count


def ingest_transcript_to_span(
    trace_id: str,
    span_id: str,
    claude_session_id: str,
    project_path: Path,
) -> bool:
    """Parse a Claude Code JSONL transcript into Langfuse observations.

    Tool calls and their results are paired by tool_use_id into single
    tool observations.  Returns True if any observations were created.
    """
    if not is_enabled():
        return False

    transcript_file = _find_transcript(claude_session_id, project_path)
    if transcript_file is None:
        log.debug("langfuse_transcript_not_found", claude_session_id=claude_session_id)
        return False

    parent = _observations.get(span_id)
    if parent is None:
        log.debug("langfuse_parent_span_not_found", span_id=span_id)
        return False

    pending_tools: dict[str, Any] = {}
    count = 0

    with open(transcript_file) as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                item = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            count += _process_transcript_item(item, parent, pending_tools)

    for tool_obs in pending_tools.values():
        tool_obs.update(metadata={"status": "no_result"})
        tool_obs.end()

    log.debug("langfuse_transcript_ingested", count=count, session=claude_session_id)
    return count > 0


# ---------------------------------------------------------------------------
# Streaming CEO transcript tailer
# ---------------------------------------------------------------------------


def _find_recent_transcript(project_path: Path, session_start: float) -> Path | None:
    """Find the most recently modified JSONL transcript after *session_start*."""
    claude_dir = Path.home() / ".claude" / "projects"
    dir_name = str(project_path.resolve()).replace("/", "-").replace(".", "-")
    proj_dir = claude_dir / dir_name
    if not proj_dir.exists():
        return None
    candidates = [
        f for f in proj_dir.glob("*.jsonl")
        if f.stat().st_mtime >= session_start
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_mtime)


class TranscriptTailer:
    """Daemon thread that tails a Claude Code transcript JSONL and
    incrementally ingests events into a Langfuse span.
    """

    POLL_INTERVAL: float = 5.0
    FIND_TIMEOUT: float = 120.0
    FIND_INTERVAL: float = 2.0

    def __init__(
        self,
        trace_id: str,
        span_id: str,
        project_path: Path,
        session_start: float,
        *,
        on_line: Callable[[bytes], None] | None = None,
    ) -> None:
        self.trace_id = trace_id
        self.span_id = span_id
        self._project_path = project_path
        self._session_start = session_start
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending_tools: dict[str, Any] = {}
        self._file_pos: int = 0
        self._transcript_path: Path | None = None
        self._total_ingested: int = 0
        self._on_line = on_line

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="ceo-transcript-tailer",
            daemon=True,
        )
        self._thread.start()

    def stop_and_drain(self) -> int:
        """Signal stop, join, do a final drain. Returns total observations ingested."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)

        if self._transcript_path is None:
            self._transcript_path = _find_recent_transcript(
                self._project_path, self._session_start,
            )
        if self._transcript_path is not None and self._transcript_path.exists():
            try:
                self._ingest_new_lines()
            except Exception:
                log.debug("tailer_final_drain_failed", exc_info=True)

        for tool_obs in self._pending_tools.values():
            try:
                tool_obs.update(metadata={"status": "no_result"})
                tool_obs.end()
            except Exception:
                pass
        self._pending_tools.clear()

        return self._total_ingested

    def _run(self) -> None:
        try:
            deadline = _time.monotonic() + self.FIND_TIMEOUT
            while not self._stop_event.is_set() and _time.monotonic() < deadline:
                self._transcript_path = _find_recent_transcript(
                    self._project_path, self._session_start,
                )
                if self._transcript_path is not None:
                    break
                self._stop_event.wait(self.FIND_INTERVAL)

            if self._transcript_path is None:
                log.debug("tailer_transcript_not_found")
                return

            while not self._stop_event.is_set():
                try:
                    self._ingest_new_lines()
                    if self.trace_id:
                        saved = _trace_names.get(self.trace_id)
                        if saved:
                            _update_trace_via_api(self.trace_id, saved[0], saved[1])
                except Exception:
                    log.debug("tailer_ingest_error", exc_info=True)
                self._stop_event.wait(self.POLL_INTERVAL)
        except Exception:
            log.debug("tailer_thread_error", exc_info=True)

    def _ingest_new_lines(self) -> None:
        if self._transcript_path is None or not self._transcript_path.exists():
            return

        with open(self._transcript_path) as f:
            f.seek(self._file_pos)
            new_lines = f.readlines()
            self._file_pos = f.tell()

        if not new_lines:
            return

        parent = _observations.get(self.span_id) if self.span_id else None

        for raw_line in new_lines:
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            if self._on_line is not None:
                try:
                    self._on_line(raw_line.encode())
                except Exception:
                    log.debug("tailer_on_line_error", exc_info=True)

            if parent is None:
                continue

            try:
                item = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            try:
                self._total_ingested += _process_transcript_item(
                    item, parent, self._pending_tools,
                )
            except Exception:
                log.debug("tailer_item_error", exc_info=True)
