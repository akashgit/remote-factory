"""Tests for parse_tools.py — verifies metric extraction from stream-JSON."""

from __future__ import annotations

from pathlib import Path

from parse_tools import parse_stream_json


SAMPLE_TRACE = """
{"type":"system","subtype":"init","timestamp":"2026-07-29T14:26:40.000Z","session_id":"test-session"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/project/.factory/config.json"}}]},"timestamp":"2026-07-29T14:26:42.000Z"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/project/.factory/strategy/current.md"}}]},"timestamp":"2026-07-29T14:26:43.000Z"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/project/src/main.py"}}]},"timestamp":"2026-07-29T14:26:44.000Z"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"factory agent researcher --task \\"study\\" --project /project"}}]},"timestamp":"2026-07-29T14:26:47.000Z"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"git log --oneline -5"}}]},"timestamp":"2026-07-29T14:26:50.000Z"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/project/.factory/reviews/researcher-latest.md"}}]},"timestamp":"2026-07-29T14:26:55.000Z"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"factory agent builder --task \\"build\\" --project /project"}}]},"timestamp":"2026-07-29T14:27:00.000Z"}
{"type":"result","result":"done","session_id":"test-session"}
""".strip()


def test_parse_stream_json_from_string() -> None:
    metrics = parse_stream_json(SAMPLE_TRACE)

    assert metrics.total_tool_calls == 7
    assert metrics.factory_read_count == 3
    assert sorted(metrics.factory_files_read) == [
        "/project/.factory/config.json",
        "/project/.factory/reviews/researcher-latest.md",
        "/project/.factory/strategy/current.md",
    ]
    assert metrics.agent_reinvocations == 2
    assert metrics.time_to_first_meaningful_action_s == 7.0


def test_parse_stream_json_empty() -> None:
    metrics = parse_stream_json("")
    assert metrics.total_tool_calls == 0
    assert metrics.factory_read_count == 0
    assert metrics.factory_files_read == []
    assert metrics.agent_reinvocations == 0
    assert metrics.time_to_first_meaningful_action_s is None


def test_parse_stream_json_malformed_lines() -> None:
    trace = 'not json\n{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/x/.factory/f"}}]},"timestamp":"2026-07-29T14:26:42.000Z"}\nalso bad'
    metrics = parse_stream_json(trace)
    assert metrics.total_tool_calls == 1
    assert metrics.factory_read_count == 1


def test_parse_stream_json_no_tool_use() -> None:
    trace = '{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]},"timestamp":"2026-07-29T14:26:42.000Z"}'
    metrics = parse_stream_json(trace)
    assert metrics.total_tool_calls == 0


def test_parse_stream_json_from_file(tmp_path: Path) -> None:
    trace_file = tmp_path / "trace.jsonl"
    trace_file.write_text(SAMPLE_TRACE)
    metrics = parse_stream_json(trace_file)
    assert metrics.total_tool_calls == 7
    assert metrics.factory_read_count == 3


def test_parse_prototype_data() -> None:
    """Verify parse_tools works against real prototype data."""
    proto_path = (
        Path(__file__).parent / "prototype-reference" / "fresh-eval" / "factory-ui" / "iter-1.jsonl"
    )
    if not proto_path.exists():
        return
    metrics = parse_stream_json(proto_path)
    assert metrics.total_tool_calls > 0
    assert metrics.factory_read_count > 0
    assert len(metrics.factory_files_read) > 0


def test_time_to_first_meaningful_action_skips_reads() -> None:
    """Verify that Read calls don't count as meaningful actions."""
    trace = """{"type":"system","subtype":"init","timestamp":"2026-07-29T14:00:00.000Z"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/a"}}]},"timestamp":"2026-07-29T14:00:01.000Z"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/b"}}]},"timestamp":"2026-07-29T14:00:02.000Z"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"TaskCreate","input":{"subject":"test"}}]},"timestamp":"2026-07-29T14:00:03.000Z"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"echo hi"}}]},"timestamp":"2026-07-29T14:00:10.000Z"}"""
    metrics = parse_stream_json(trace)
    assert metrics.time_to_first_meaningful_action_s == 10.0
