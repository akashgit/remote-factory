"""Session statefulness — consolidate .factory/ state on interrupt, inject on resume."""

from __future__ import annotations

import csv
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

_SUMMARY_FILE = "session_summary.md"
_SUMMARY_DIR = "state"
_MAX_SUMMARY_CHARS = 2000
_STALE_HOURS = 24


def _summary_path(project_path: Path) -> Path:
    return project_path / ".factory" / _SUMMARY_DIR / _SUMMARY_FILE


def _is_stale(summary_path: Path, max_hours: int = _STALE_HOURS) -> bool:
    """True if file mtime is older than max_hours."""
    try:
        mtime = summary_path.stat().st_mtime
    except OSError:
        return True
    age_hours = (time.time() - mtime) / 3600
    return age_hours > max_hours


def _read_json_safe(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _read_text_safe(path: Path, max_chars: int = 500) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text()[:max_chars]
    except OSError:
        return None


def _summarize_cycle_state(project_path: Path) -> list[str]:
    data = _read_json_safe(project_path / ".factory" / "state" / "cycle.json")
    if not data:
        return []
    lines = ["### Cycle State"]
    if mode := data.get("mode"):
        lines.append(f"- Mode: {mode}")
    if started := data.get("started_at"):
        lines.append(f"- Started: {started}")
    if (respawns := data.get("respawns")) is not None:
        lines.append(f"- Respawns: {respawns}")
    if cycle_id := data.get("cycle_id"):
        lines.append(f"- Cycle ID: {cycle_id}")
    return lines


def _summarize_checkpoint(project_path: Path) -> list[str]:
    data = _read_json_safe(project_path / ".factory" / "checkpoint.json")
    if not data:
        return []
    lines = ["### Checkpoint"]
    if completed := data.get("completed_agents"):
        lines.append(f"- Completed agents: {', '.join(completed)}")
    if pending := data.get("pending_agents"):
        lines.append(f"- Pending agents: {', '.join(pending)}")
    if hyp := data.get("current_hypothesis"):
        lines.append(f"- Current hypothesis: {hyp}")
    scores = data.get("last_eval_scores")
    if scores:
        score_str = ", ".join(f"{k}={v:.3f}" for k, v in scores.items())
        lines.append(f"- Eval scores: {score_str}")
    return lines


def _summarize_recent_events(project_path: Path) -> list[str]:
    events_path = project_path / ".factory" / "events.jsonl"
    if not events_path.is_file():
        return []
    lines = ["### Agent Timeline"]
    try:
        raw_lines = events_path.read_text().splitlines()
    except OSError:
        return []
    agent_events: list[dict[str, Any]] = []
    for raw in raw_lines:
        if not raw.strip():
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        etype = ev.get("type", "")
        if etype.startswith("agent."):
            agent_events.append(ev)
    for ev in agent_events[-10:]:
        role = ev.get("agent", "?")
        etype = ev.get("type", "")
        ts = ev.get("timestamp", "")[:19]
        status = etype.split(".")[-1] if "." in etype else etype
        entry = f"- [{ts}] {role}: {status}"
        duration = ev.get("data", {}).get("duration_s")
        if duration is not None:
            entry += f" ({duration:.0f}s)"
        lines.append(entry)
    if not agent_events:
        return []
    return lines


def _summarize_reviews(project_path: Path) -> list[str]:
    reviews_dir = project_path / ".factory" / "reviews"
    if not reviews_dir.is_dir():
        return []
    lines = ["### Review Summaries"]
    found = False
    try:
        review_files = sorted(reviews_dir.iterdir())
    except OSError:
        return []
    for f in review_files:
        if not f.name.endswith(".md") or not f.is_file():
            continue
        content = _read_text_safe(f, max_chars=200)
        if content:
            first_line = content.strip().split("\n")[0][:150]
            lines.append(f"- {f.name}: {first_line}")
            found = True
    return lines if found else []


def _summarize_strategy(project_path: Path) -> list[str]:
    content = _read_text_safe(project_path / ".factory" / "strategy" / "current.md", max_chars=500)
    if not content:
        return []
    return ["### Strategy (truncated)", content.strip()[:400]]


def _summarize_results(project_path: Path) -> list[str]:
    tsv_path = project_path / ".factory" / "results.tsv"
    if not tsv_path.is_file():
        return []
    lines = ["### Recent Experiments"]
    try:
        with open(tsv_path, newline="") as f:
            reader = csv.DictReader(f, dialect="excel-tab")
            rows = list(reader)
    except (OSError, csv.Error):
        return []
    for row in rows[-5:]:
        exp_id = row.get("id", "?")
        verdict = row.get("verdict", "?")
        delta = row.get("delta", "?")
        hyp = row.get("hypothesis", "")[:60]
        lines.append(f"- #{exp_id}: {verdict} (delta={delta}) — {hyp}")
    return lines if len(lines) > 1 else []


def generate_session_summary(project_path: Path) -> str:
    """Read .factory/ state and produce a markdown summary for CEO resume context."""
    sections: list[list[str]] = [
        ["## Previous Session State"],
        _summarize_cycle_state(project_path),
        _summarize_checkpoint(project_path),
        _summarize_recent_events(project_path),
        _summarize_reviews(project_path),
        _summarize_strategy(project_path),
        _summarize_results(project_path),
    ]
    parts = ["\n".join(s) for s in sections if s]
    summary = "\n\n".join(parts)
    if not summary.strip() or summary.strip() == "## Previous Session State":
        return "## Previous Session State\n\nNo prior state found."
    return summary


def save_session_summary(project_path: Path) -> None:
    """Generate and atomically write session summary. Never raises."""
    try:
        summary = generate_session_summary(project_path)
        if len(summary) > _MAX_SUMMARY_CHARS:
            summary = summary[: _MAX_SUMMARY_CHARS - 20] + "\n\n[truncated]"

        dest = _summary_path(project_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp = tempfile.mkstemp(dir=str(dest.parent), suffix=".tmp", prefix="session_")
        try:
            os.write(fd, summary.encode())
            os.close(fd)
            os.replace(tmp, str(dest))
        except BaseException:
            os.close(fd) if not _fd_closed(fd) else None
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        log.info("session_summary_saved", path=str(dest), chars=len(summary))
    except Exception as exc:
        log.warning("session_summary_save_failed", error=str(exc))


def _fd_closed(fd: int) -> bool:
    try:
        os.fstat(fd)
        return False
    except OSError:
        return True


def clear_session_summary(project_path: Path) -> bool:
    """Delete session_summary.md if it exists. Returns True if deleted."""
    path = _summary_path(project_path)
    if path.exists():
        try:
            path.unlink()
            log.info("session_summary_cleared", path=str(path))
            return True
        except OSError as exc:
            log.warning("session_summary_clear_failed", error=str(exc))
            return False
    return False


def load_summary_if_fresh(project_path: Path, cycle_id: str | None = None) -> str | None:
    """Load session summary if it exists, is fresh (<24h), and cycle_id matches."""
    path = _summary_path(project_path)
    if not path.is_file():
        return None
    if _is_stale(path):
        log.debug("session_summary_stale", path=str(path))
        return None
    try:
        content = path.read_text()
    except OSError:
        return None
    if cycle_id and f"Cycle ID: {cycle_id}" not in content:
        log.debug(
            "session_summary_cycle_mismatch",
            expected=cycle_id,
            path=str(path),
        )
        return None
    return content
