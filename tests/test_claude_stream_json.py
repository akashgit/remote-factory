"""Tests for _parse_stream_json() — Claude Code JSONL output parser."""

import json

import pytest

from factory.runners.claude import _parse_stream_json
from factory.runners.types import (
    ExecutionTrace,
    ToolCallStatus,
    ToolKind,
    UsageStats,
)


def _make_jsonl(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events)


# -- Fixtures (canned JSONL) ------------------------------------------------

SIMPLE_TASK = _make_jsonl(
    {"type": "system", "session_id": "abc-123"},
    {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "main.py"},
                }
            ]
        },
    },
    {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "file contents",
                }
            ]
        },
    },
    {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Fixed the bug."}]},
    },
    {
        "type": "result",
        "result": "Fixed the bug.",
        "usage": {"input_tokens": 1000, "output_tokens": 500},
        "cost_usd": 0.05,
        "duration_ms": 12000,
        "model": "claude-sonnet-4-6",
        "session_id": "abc-123",
    },
)

MULTIPLE_TOOLS = _make_jsonl(
    {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "a.py"},
                },
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "b.py"},
                },
            ]
        },
    },
    {
        "type": "result",
        "result": "Done.",
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "cost_usd": 0.01,
        "duration_ms": 1000,
        "model": "claude-sonnet-4-6",
        "session_id": "sess-multi",
    },
)

TOOL_ERROR = _make_jsonl(
    {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "missing.py"},
                }
            ]
        },
    },
    {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t-err",
                    "content": "File not found: missing.py",
                    "is_error": True,
                }
            ]
        },
    },
    {
        "type": "result",
        "result": "Could not read file.",
        "usage": {"input_tokens": 50, "output_tokens": 20},
        "cost_usd": 0.001,
        "duration_ms": 500,
        "model": "claude-sonnet-4-6",
        "session_id": "sess-err",
    },
)

EDIT_TRACKING = _make_jsonl(
    {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {"file_path": "src/main.py", "old_string": "x", "new_string": "y"},
                },
                {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": "src/new.py", "content": "code"},
                },
            ]
        },
    },
    {
        "type": "result",
        "result": "Files updated.",
        "usage": {"input_tokens": 200, "output_tokens": 100},
        "cost_usd": 0.02,
        "duration_ms": 3000,
        "model": "claude-sonnet-4-6",
        "session_id": "sess-edit",
    },
)

COMMAND_TRACKING = _make_jsonl(
    {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "pytest -v"},
                },
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "ruff check ."},
                },
            ]
        },
    },
    {
        "type": "result",
        "result": "Tests passed.",
        "usage": {"input_tokens": 300, "output_tokens": 150},
        "cost_usd": 0.03,
        "duration_ms": 5000,
        "model": "claude-sonnet-4-6",
        "session_id": "sess-cmd",
    },
)

THINKING_BLOCKS = _make_jsonl(
    {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "thinking", "thinking": "Let me analyze the code..."},
                {"type": "text", "text": "Here is the fix."},
            ]
        },
    },
    {
        "type": "result",
        "result": "Here is the fix.",
        "usage": {"input_tokens": 400, "output_tokens": 200},
        "cost_usd": 0.04,
        "duration_ms": 8000,
        "model": "claude-sonnet-4-6",
        "session_id": "sess-think",
    },
)

EMPTY_RESPONSE = _make_jsonl(
    {
        "type": "result",
        "result": "No changes needed.",
        "usage": {"input_tokens": 50, "output_tokens": 10},
        "cost_usd": 0.001,
        "duration_ms": 200,
        "model": "claude-sonnet-4-6",
        "session_id": "sess-empty",
    },
)


# -- Tests -------------------------------------------------------------------


class TestSimpleTask:
    def test_final_text(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(SIMPLE_TASK)
        assert text == "Fixed the bug."

    def test_session_id(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(SIMPLE_TASK)
        assert session_id == "abc-123"

    def test_steps_count(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(SIMPLE_TASK)
        assert len(trace.steps) == 2  # two assistant turns

    def test_first_step_has_tool_call(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(SIMPLE_TASK)
        step0 = trace.steps[0]
        assert len(step0.tool_calls) == 1
        assert step0.tool_calls[0].tool_name == "Read"
        assert step0.tool_calls[0].kind == ToolKind.READ

    def test_tool_result_populates_output(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(SIMPLE_TASK)
        tc = trace.steps[0].tool_calls[0]
        assert tc.output_summary == "file contents"
        assert tc.status == ToolCallStatus.COMPLETED

    def test_second_step_has_text(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(SIMPLE_TASK)
        assert trace.steps[1].output_text == "Fixed the bug."

    def test_files_read(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(SIMPLE_TASK)
        assert "main.py" in trace.files_read

    def test_usage_present(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(SIMPLE_TASK)
        assert usage is not None


class TestMultipleTools:
    def test_parallel_tool_calls(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(MULTIPLE_TOOLS)
        assert len(trace.steps) == 1
        step = trace.steps[0]
        assert len(step.tool_calls) == 2
        assert step.tool_calls[0].tool_name == "Read"
        assert step.tool_calls[1].tool_name == "Read"

    def test_both_files_tracked(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(MULTIPLE_TOOLS)
        assert "a.py" in trace.files_read
        assert "b.py" in trace.files_read


class TestToolError:
    def test_error_status(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(TOOL_ERROR)
        tc = trace.steps[0].tool_calls[0]
        assert tc.status == ToolCallStatus.FAILED

    def test_error_message(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(TOOL_ERROR)
        tc = trace.steps[0].tool_calls[0]
        assert tc.error is not None
        assert "File not found" in tc.error


class TestEditTracking:
    def test_files_written(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(EDIT_TRACKING)
        assert "src/main.py" in trace.files_written
        assert "src/new.py" in trace.files_written

    def test_edit_tool_kind(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(EDIT_TRACKING)
        step = trace.steps[0]
        assert step.tool_calls[0].kind == ToolKind.EDIT
        assert step.tool_calls[1].kind == ToolKind.EDIT


class TestCommandTracking:
    def test_commands_executed(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(COMMAND_TRACKING)
        assert "pytest -v" in trace.commands_executed
        assert "ruff check ." in trace.commands_executed

    def test_bash_tool_kind(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(COMMAND_TRACKING)
        step = trace.steps[0]
        assert step.tool_calls[0].kind == ToolKind.EXECUTE
        assert step.tool_calls[1].kind == ToolKind.EXECUTE


class TestThinkingBlocks:
    def test_thinking_captured(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(THINKING_BLOCKS)
        assert len(trace.thinking_blocks) == 1
        assert "analyze the code" in trace.thinking_blocks[0]

    def test_text_still_captured(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(THINKING_BLOCKS)
        assert trace.steps[0].output_text == "Here is the fix."


class TestEmptyResponse:
    def test_no_steps(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(EMPTY_RESPONSE)
        assert len(trace.steps) == 0

    def test_final_text(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(EMPTY_RESPONSE)
        assert text == "No changes needed."

    def test_usage_still_present(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(EMPTY_RESPONSE)
        assert usage is not None


class TestToolKindMapping:
    """Verify all Claude tool names map to correct ToolKind values."""

    @pytest.mark.parametrize(
        "tool_name,expected_kind",
        [
            ("Read", ToolKind.READ),
            ("Edit", ToolKind.EDIT),
            ("Write", ToolKind.EDIT),
            ("MultiEdit", ToolKind.EDIT),
            ("NotebookEdit", ToolKind.EDIT),
            ("Bash", ToolKind.EXECUTE),
            ("Grep", ToolKind.SEARCH),
            ("Glob", ToolKind.SEARCH),
            ("WebFetch", ToolKind.FETCH),
            ("WebSearch", ToolKind.FETCH),
            ("Agent", ToolKind.OTHER),
            ("TodoWrite", ToolKind.OTHER),
            ("Task", ToolKind.OTHER),
            ("UnknownTool", ToolKind.OTHER),
        ],
    )
    def test_tool_kind_mapping(self, tool_name: str, expected_kind: ToolKind) -> None:
        jsonl = _make_jsonl(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": tool_name, "input": {}}
                    ]
                },
            },
            {
                "type": "result",
                "result": "done",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "cost_usd": 0.001,
                "duration_ms": 100,
                "model": "claude-sonnet-4-6",
                "session_id": "sess-kind",
            },
        )
        text, trace, usage, session_id = _parse_stream_json(jsonl)
        assert trace.steps[0].tool_calls[0].kind == expected_kind


class TestUsageExtraction:
    def test_input_tokens(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(SIMPLE_TASK)
        assert usage is not None
        assert usage.input_tokens == 1000

    def test_output_tokens(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(SIMPLE_TASK)
        assert usage is not None
        assert usage.output_tokens == 500

    def test_total_tokens(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(SIMPLE_TASK)
        assert usage is not None
        assert usage.total_tokens == 1500

    def test_cost_usd(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(SIMPLE_TASK)
        assert usage is not None
        assert usage.cost_usd == 0.05

    def test_duration_seconds(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(SIMPLE_TASK)
        assert usage is not None
        assert usage.duration_seconds == 12.0

    def test_model_used(self) -> None:
        text, trace, usage, session_id = _parse_stream_json(SIMPLE_TASK)
        assert usage is not None
        assert usage.model_used == "claude-sonnet-4-6"


class TestEdgeCases:
    def test_blank_lines_ignored(self) -> None:
        jsonl = "\n\n" + SIMPLE_TASK + "\n\n"
        text, trace, usage, session_id = _parse_stream_json(jsonl)
        assert text == "Fixed the bug."

    def test_malformed_json_lines_skipped(self) -> None:
        jsonl = "not json\n" + SIMPLE_TASK
        text, trace, usage, session_id = _parse_stream_json(jsonl)
        assert text == "Fixed the bug."

    def test_tool_result_list_content(self) -> None:
        """tool_result with list content (multiple text blocks) is handled."""
        jsonl = _make_jsonl(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "x.py"}}
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t-list",
                            "content": [
                                {"type": "text", "text": "line1"},
                                {"type": "text", "text": "line2"},
                            ],
                        }
                    ]
                },
            },
            {
                "type": "result",
                "result": "ok",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "cost_usd": 0.001,
                "duration_ms": 100,
                "model": "claude-sonnet-4-6",
                "session_id": "sess-list",
            },
        )
        text, trace, usage, session_id = _parse_stream_json(jsonl)
        tc = trace.steps[0].tool_calls[0]
        assert tc.output_summary is not None
        assert "line1" in tc.output_summary

    def test_file_location_extracted(self) -> None:
        """FileLocation populated from tool_use input."""
        text, trace, usage, session_id = _parse_stream_json(SIMPLE_TASK)
        tc = trace.steps[0].tool_calls[0]
        assert len(tc.locations) == 1
        assert tc.locations[0].path == "main.py"
