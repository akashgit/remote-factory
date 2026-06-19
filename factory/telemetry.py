"""Langfuse tracing wrapper — graceful no-op when not configured."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

try:
    from langfuse import Langfuse

    _HAS_LANGFUSE = True
except ImportError:
    _HAS_LANGFUSE = False

_client: object | None = None
_observations: dict[str, Any] = {}


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


def begin_trace(
    project_name: str,
    cycle_id: str | None = None,
    model: str | None = None,
) -> str | None:
    """Create a root trace (as a span observation) and return its ID."""
    if not is_enabled():
        return None
    client = _get_client()
    obs = client.start_observation(
        name=f"factory:{project_name}/{cycle_id or 'cycle'}",
        as_type="span",
        input={"project": project_name, "cycle_id": cycle_id},
        metadata={"model": model, "project": project_name},
    )
    _observations[obs.id] = obs
    log.debug("langfuse_trace_started", trace_id=obs.trace_id, obs_id=obs.id)
    return obs.id


def begin_span(
    trace_id: str,
    parent_span_id: str | None,
    role: str,
    model: str | None = None,
) -> str | None:
    """Create a child span under a parent and return its span_id."""
    if not is_enabled():
        return None
    parent = _observations.get(parent_span_id or trace_id)
    if parent is None:
        client = _get_client()
        parent = client.start_observation(
            name=f"agent:{role}",
            as_type="span",
            metadata={"role": role, "model": model},
        )
        _observations[parent.id] = parent
        return parent.id

    obs = parent.start_observation(
        name=f"agent:{role}",
        as_type="span",
        metadata={"role": role, "model": model},
    )
    _observations[obs.id] = obs
    log.debug("langfuse_span_started", span_id=obs.id, role=role)
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

    usage_details: dict[str, int] = {}
    if usage is not None:
        input_t = getattr(usage, "input_tokens", 0) or 0
        output_t = getattr(usage, "output_tokens", 0) or 0
        usage_details = {
            "input": input_t,
            "output": output_t,
            "cache_read_input_tokens": getattr(usage, "cache_read_tokens", 0) or 0,
            "total": input_t + output_t,
        }
        meta["total_cost_usd"] = getattr(usage, "total_cost_usd", 0.0) or 0.0
        meta["duration_ms"] = getattr(usage, "duration_ms", 0.0) or 0.0
        meta["num_turns"] = getattr(usage, "num_turns", 0) or 0
        meta["model"] = getattr(usage, "model", None)

    obs.update(
        output=output,
        metadata=meta,
        usage_details=usage_details or None,
    )
    obs.end()
    _observations.pop(span_id, None)
    log.debug("langfuse_span_ended", span_id=span_id, status=status)


def end_trace(trace_id: str) -> None:
    """Mark a root trace as finished."""
    if not is_enabled() or not trace_id:
        return
    obs = _observations.get(trace_id)
    if obs is not None:
        obs.update(output={"status": "completed"})
        obs.end()
        _observations.pop(trace_id, None)
    log.debug("langfuse_trace_ended", trace_id=trace_id)


def flush() -> None:
    """Flush any buffered Langfuse events."""
    if _client is not None:
        _get_client().flush()


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


def ingest_transcript_to_span(
    trace_id: str,
    span_id: str,
    claude_session_id: str,
    project_path: Path,
) -> bool:
    """Parse a Claude Code JSONL transcript into Langfuse observations.

    Tool calls and their results are paired by tool_use_id into single
    span observations.  Returns True if any observations were created.
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
                        tool_obs.update(output=tr["content"][:4000])
                        tool_obs.end()
                        count += 1
                    else:
                        parent.create_event(
                            name="tool_output",
                            output=tr["content"][:4000],
                            metadata={"tool_use_id": tool_use_id},
                        )
                        count += 1

                if not tool_results and text_parts:
                    text = "".join(text_parts)
                    if text.strip():
                        parent.create_event(
                            name="user_message",
                            input=text[:4000],
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
                                output=text[:4000],
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
                                metadata={"thinking": text[:4000]},
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
                        tool_obs.update(output=text[:4000])
                        tool_obs.end()
                    else:
                        parent.create_event(
                            name="tool_output",
                            output=text[:4000],
                        )
                    count += 1

    for tool_obs in pending_tools.values():
        tool_obs.update(metadata={"status": "no_result"})
        tool_obs.end()

    log.debug("langfuse_transcript_ingested", count=count, session=claude_session_id)
    return count > 0
