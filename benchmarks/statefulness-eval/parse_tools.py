"""Stream-JSON trace parser for Claude Code CEO session output.

Extracts 5 statefulness metrics from `--output-format stream-json --verbose` output:
1. .factory/ read count
2. Files read list (deduplicated .factory/ paths)
3. Agent re-invocations
4. Time-to-first-meaningful-action
5. Total tool calls
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import structlog

log = structlog.get_logger()


@dataclass
class TraceMetrics:
    factory_read_count: int = 0
    factory_files_read: list[str] = field(default_factory=list)
    agent_reinvocations: int = 0
    time_to_first_meaningful_action_s: float | None = None
    total_tool_calls: int = 0
    raw_tool_calls: list[dict] = field(default_factory=list)


def _extract_tool_calls_from_content(content: list[dict]) -> list[dict]:
    """Extract tool_use blocks from an assistant message's content array."""
    calls = []
    for block in content:
        if block.get("type") != "tool_use":
            continue
        name = block.get("name", "unknown")
        input_data = block.get("input", {})
        calls.append({"name": name, "input": input_data})
    return calls


def _is_factory_read(name: str, input_data: dict) -> bool:
    """Check if a tool call is a Read targeting a .factory/ path."""
    if name != "Read":
        return False
    file_path = input_data.get("file_path", "")
    return ".factory/" in file_path


def _is_agent_invocation(name: str, input_data: dict) -> bool:
    """Check if a tool call is a Bash command invoking factory agent."""
    if name != "Bash":
        return False
    command = input_data.get("command", "")
    return "factory agent" in command


def _is_meaningful_action(name: str) -> bool:
    """A meaningful action is any tool call that is NOT a Read or system operation."""
    non_meaningful = {"Read", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet"}
    return name not in non_meaningful


def _parse_timestamp(ts_str: str) -> datetime | None:
    """Parse an ISO 8601 timestamp from stream-JSON events."""
    if not ts_str:
        return None
    try:
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def parse_stream_json(source: str | Path) -> TraceMetrics:
    """Parse a stream-JSON file or string and extract trace metrics.

    Args:
        source: Path to a JSONL file, or the raw JSONL string content.

    Returns:
        TraceMetrics with all 5 metrics populated.
    """
    metrics = TraceMetrics()
    factory_files_seen: set[str] = set()
    first_event_ts: datetime | None = None
    first_meaningful_ts: datetime | None = None

    if isinstance(source, Path) or (
        isinstance(source, str) and "\n" not in source and len(source) < 4096
    ):
        path = Path(source)
        if path.is_file():
            lines = path.read_text().splitlines()
        else:
            lines = source.splitlines()
    else:
        lines = source.splitlines()

    for line_num, raw_line in enumerate(lines, 1):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            log.warning("malformed_json_line", line=line_num)
            continue

        event_type = event.get("type")
        timestamp = _parse_timestamp(event.get("timestamp", ""))

        if timestamp and first_event_ts is None:
            first_event_ts = timestamp

        if event_type != "assistant":
            continue

        message = event.get("message", {})
        content = message.get("content", [])
        tool_calls = _extract_tool_calls_from_content(content)

        for call in tool_calls:
            name = call["name"]
            input_data = call["input"]
            metrics.total_tool_calls += 1
            metrics.raw_tool_calls.append(call)

            if _is_factory_read(name, input_data):
                metrics.factory_read_count += 1
                file_path = input_data.get("file_path", "")
                factory_files_seen.add(file_path)

            if _is_agent_invocation(name, input_data):
                metrics.agent_reinvocations += 1

            if (
                _is_meaningful_action(name)
                and first_meaningful_ts is None
                and timestamp is not None
            ):
                first_meaningful_ts = timestamp

    metrics.factory_files_read = sorted(factory_files_seen)

    if first_event_ts and first_meaningful_ts:
        delta = (first_meaningful_ts - first_event_ts).total_seconds()
        metrics.time_to_first_meaningful_action_s = max(0.0, delta)

    return metrics


def main() -> None:
    """CLI entry point for quick trace inspection."""
    if len(sys.argv) < 2:
        print("Usage: python parse_tools.py <trace.jsonl>", file=sys.stderr)
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"Error: {path} not found", file=sys.stderr)
        sys.exit(1)

    metrics = parse_stream_json(path)
    print(f"TOTAL_TOOL_CALLS={metrics.total_tool_calls}")
    print(f"FACTORY_READS={metrics.factory_read_count}")
    print(f"FACTORY_FILES={metrics.factory_files_read}")
    print(f"AGENT_REINVOCATIONS={metrics.agent_reinvocations}")
    print(f"TIME_TO_FIRST_ACTION={metrics.time_to_first_meaningful_action_s}")
    print("\n--- Tool call sequence ---")
    for i, call in enumerate(metrics.raw_tool_calls, 1):
        name = call["name"]
        target = ""
        if name == "Read":
            target = call["input"].get("file_path", "")
        elif name == "Bash":
            target = call["input"].get("command", "")[:120]
        elif name in ("Write", "Edit"):
            target = call["input"].get("file_path", "")
        marker = " [.factory/]" if _is_factory_read(name, call["input"]) else ""
        print(f"  {i:3d}. {name:15s} {target[:100]}{marker}")


if __name__ == "__main__":
    main()
