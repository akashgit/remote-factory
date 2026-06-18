"""Tests for factory_tracing.executor — stream-json parsing and span creation."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from factory_tracing import provider as _provider_mod
from factory_tracing.executor import run_traced_agent, AgentResult


INIT_EVENT = {
    "type": "system",
    "subtype": "init",
    "model": "claude-opus-4-6[1m]",
    "session_id": "sess-abc123",
    "tools": ["Read", "Bash", "Edit"],
}

ASSISTANT_TEXT_EVENT = {
    "type": "assistant",
    "message": {
        "model": "claude-opus-4-6[1m]",
        "content": [{"type": "text", "text": "Here is the answer to your question."}],
        "usage": {"input_tokens": 100, "output_tokens": 50},
    },
    "request_id": "req-001",
}

ASSISTANT_TOOL_USE_EVENT = {
    "type": "assistant",
    "message": {
        "model": "claude-opus-4-6[1m]",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_abc123",
                "name": "Read",
                "input": {"file_path": "/tmp/test.py"},
            }
        ],
        "usage": {"input_tokens": 80, "output_tokens": 30},
    },
    "request_id": "req-002",
}

TOOL_RESULT_EVENT = {
    "type": "user",
    "message": {
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_abc123",
                "content": "def hello():\n    print('world')",
            }
        ]
    },
}

RESULT_EVENT = {
    "type": "result",
    "subtype": "success",
    "result": "The file contains a hello function.",
    "usage": {"input_tokens": 200, "output_tokens": 100},
    "total_cost_usd": 0.06,
    "duration_ms": 12162,
    "num_turns": 2,
}


@pytest.fixture(autouse=True)
def tracing_setup():
    exporter = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exporter))
    _provider_mod._provider = tp
    yield exporter
    tp.shutdown()
    _provider_mod._provider = None


def _attrs(span) -> dict:
    return dict(span.attributes) if span.attributes else {}


def _make_mock_popen(events: list[dict], returncode: int = 0):
    lines = [json.dumps(e) + "\n" for e in events]
    mock_proc = MagicMock()
    mock_proc.stdout = iter(lines)
    mock_proc.stderr = MagicMock()
    mock_proc.returncode = returncode
    mock_proc.wait.return_value = returncode
    return mock_proc


def _run_with_events(events: list[dict], returncode: int = 0, **kwargs):
    mock_proc = _make_mock_popen(events, returncode)
    with patch("factory_tracing.executor.subprocess.Popen", return_value=mock_proc):
        return run_traced_agent(
            prompt=kwargs.get("prompt", "test prompt"),
            role=kwargs.get("role", "researcher"),
            run_id=kwargs.get("run_id", "run-1"),
            project_name=kwargs.get("project_name", "test-project"),
        )


class TestParseStreamJsonEvents:
    def test_init_event_sets_model_and_session(self, tracing_setup):
        result = _run_with_events([INIT_EVENT, RESULT_EVENT])
        assert result.model == "claude-opus-4-6[1m]"
        assert result.session_id == "sess-abc123"

    def test_result_event_sets_final_values(self, tracing_setup):
        result = _run_with_events([INIT_EVENT, RESULT_EVENT])
        assert result.response_text == "The file contains a hello function."
        assert result.input_tokens == 200
        assert result.output_tokens == 100
        assert result.cost_usd == 0.06
        assert result.duration_ms == 12162
        assert result.num_turns == 2

    def test_malformed_json_lines_skipped(self, tracing_setup):
        events = [INIT_EVENT]
        mock_proc = MagicMock()
        mock_proc.stdout = iter([
            json.dumps(INIT_EVENT) + "\n",
            "not valid json\n",
            "\n",
            json.dumps(RESULT_EVENT) + "\n",
        ])
        mock_proc.stderr = MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        with patch("factory_tracing.executor.subprocess.Popen", return_value=mock_proc):
            result = run_traced_agent("prompt", "role", "run-1", "proj")
        assert result.response_text == "The file contains a hello function."

    def test_agent_span_created_with_correct_name(self, tracing_setup):
        _run_with_events([INIT_EVENT, RESULT_EVENT], role="builder")
        spans = tracing_setup.get_finished_spans()
        agent_spans = [s for s in spans if s.name == "invoke_agent builder"]
        assert len(agent_spans) == 1

    def test_agent_span_has_all_attributes(self, tracing_setup):
        _run_with_events([INIT_EVENT, RESULT_EVENT], role="researcher", run_id="run-42")
        spans = tracing_setup.get_finished_spans()
        agent_span = next(s for s in spans if "invoke_agent" in s.name)
        attrs = _attrs(agent_span)
        assert attrs["gen_ai.operation.name"] == "invoke_agent"
        assert attrs["gen_ai.agent.name"] == "researcher"
        assert attrs["gen_ai.system"] == "anthropic"
        assert attrs["factory.run.id"] == "run-42"
        assert attrs["langfuse.observation.type"] == "span"


class TestToolSpansHaveInputOutput:
    def test_tool_span_created_with_input(self, tracing_setup):
        _run_with_events([INIT_EVENT, ASSISTANT_TOOL_USE_EVENT, TOOL_RESULT_EVENT, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        tool_spans = [s for s in spans if s.name == "tool:Read"]
        assert len(tool_spans) == 1
        attrs = _attrs(tool_spans[0])
        assert attrs["tool.name"] == "Read"
        assert "/tmp/test.py" in attrs["gen_ai.prompt"]

    def test_tool_span_has_result_output(self, tracing_setup):
        _run_with_events([INIT_EVENT, ASSISTANT_TOOL_USE_EVENT, TOOL_RESULT_EVENT, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        tool_spans = [s for s in spans if s.name == "tool:Read"]
        attrs = _attrs(tool_spans[0])
        assert "def hello():" in attrs["gen_ai.completion"]

    def test_tool_span_has_langfuse_io(self, tracing_setup):
        _run_with_events([INIT_EVENT, ASSISTANT_TOOL_USE_EVENT, TOOL_RESULT_EVENT, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        tool_spans = [s for s in spans if s.name == "tool:Read"]
        attrs = _attrs(tool_spans[0])
        assert "langfuse.span.input" in attrs
        assert "langfuse.span.output" in attrs

    def test_tool_span_is_child_of_agent(self, tracing_setup):
        _run_with_events([INIT_EVENT, ASSISTANT_TOOL_USE_EVENT, TOOL_RESULT_EVENT, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        agent_span = next(s for s in spans if "invoke_agent" in s.name)
        tool_span = next(s for s in spans if s.name == "tool:Read")
        assert tool_span.parent is not None
        assert tool_span.parent.span_id == agent_span.context.span_id

    def test_multiple_tool_uses_tracked_independently(self, tracing_setup):
        tool_event_1 = {
            "type": "assistant",
            "message": {
                "model": "claude-opus-4-6[1m]",
                "content": [
                    {"type": "tool_use", "id": "toolu_001", "name": "Read", "input": {"file_path": "/a.py"}},
                    {"type": "tool_use", "id": "toolu_002", "name": "Bash", "input": {"command": "ls"}},
                ],
                "usage": {"input_tokens": 50, "output_tokens": 20},
            },
        }
        result_1 = {
            "type": "user",
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": "toolu_001", "content": "file A content"},
                {"type": "tool_result", "tool_use_id": "toolu_002", "content": "dir listing"},
            ]},
        }
        _run_with_events([INIT_EVENT, tool_event_1, result_1, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        tool_spans = [s for s in spans if s.name.startswith("tool:")]
        assert len(tool_spans) == 2
        names = {s.name for s in tool_spans}
        assert names == {"tool:Read", "tool:Bash"}


class TestLlmSpansHaveContent:
    def test_llm_span_created_for_text_response(self, tracing_setup):
        _run_with_events([INIT_EVENT, ASSISTANT_TEXT_EVENT, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        llm_spans = [s for s in spans if s.name == "llm_call"]
        assert len(llm_spans) == 1

    def test_llm_span_has_model(self, tracing_setup):
        _run_with_events([INIT_EVENT, ASSISTANT_TEXT_EVENT, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        llm_span = next(s for s in spans if s.name == "llm_call")
        attrs = _attrs(llm_span)
        assert attrs["gen_ai.request.model"] == "claude-opus-4-6[1m]"

    def test_llm_span_has_completion_text(self, tracing_setup):
        _run_with_events([INIT_EVENT, ASSISTANT_TEXT_EVENT, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        llm_span = next(s for s in spans if s.name == "llm_call")
        attrs = _attrs(llm_span)
        assert "Here is the answer" in attrs["gen_ai.completion"]

    def test_llm_span_has_prompt(self, tracing_setup):
        _run_with_events([INIT_EVENT, ASSISTANT_TEXT_EVENT, RESULT_EVENT], prompt="my question")
        spans = tracing_setup.get_finished_spans()
        llm_span = next(s for s in spans if s.name == "llm_call")
        attrs = _attrs(llm_span)
        assert attrs["gen_ai.prompt"] == "my question"

    def test_llm_span_has_token_counts(self, tracing_setup):
        _run_with_events([INIT_EVENT, ASSISTANT_TEXT_EVENT, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        llm_span = next(s for s in spans if s.name == "llm_call")
        attrs = _attrs(llm_span)
        assert attrs["gen_ai.usage.input_tokens"] == 100
        assert attrs["gen_ai.usage.output_tokens"] == 50

    def test_llm_span_is_child_of_agent(self, tracing_setup):
        _run_with_events([INIT_EVENT, ASSISTANT_TEXT_EVENT, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        agent_span = next(s for s in spans if "invoke_agent" in s.name)
        llm_span = next(s for s in spans if s.name == "llm_call")
        assert llm_span.parent is not None
        assert llm_span.parent.span_id == agent_span.context.span_id

    def test_no_llm_span_for_tool_use_response(self, tracing_setup):
        _run_with_events([INIT_EVENT, ASSISTANT_TOOL_USE_EVENT, TOOL_RESULT_EVENT, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        llm_spans = [s for s in spans if s.name == "llm_call"]
        assert len(llm_spans) == 0


class TestAgentSpanHasFinalResult:
    def test_agent_span_has_completion(self, tracing_setup):
        _run_with_events([INIT_EVENT, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        agent_span = next(s for s in spans if "invoke_agent" in s.name)
        attrs = _attrs(agent_span)
        assert "hello function" in attrs["gen_ai.completion"]

    def test_agent_span_has_langfuse_output(self, tracing_setup):
        _run_with_events([INIT_EVENT, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        agent_span = next(s for s in spans if "invoke_agent" in s.name)
        attrs = _attrs(agent_span)
        assert "hello function" in attrs["langfuse.span.output"]

    def test_agent_span_has_prompt_as_input(self, tracing_setup):
        _run_with_events([INIT_EVENT, RESULT_EVENT], prompt="do something")
        spans = tracing_setup.get_finished_spans()
        agent_span = next(s for s in spans if "invoke_agent" in s.name)
        attrs = _attrs(agent_span)
        assert attrs["gen_ai.prompt"] == "do something"
        assert attrs["langfuse.span.input"] == "do something"

    def test_agent_span_has_token_counts(self, tracing_setup):
        _run_with_events([INIT_EVENT, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        agent_span = next(s for s in spans if "invoke_agent" in s.name)
        attrs = _attrs(agent_span)
        assert attrs["gen_ai.usage.input_tokens"] == 200
        assert attrs["gen_ai.usage.output_tokens"] == 100
        assert attrs["gen_ai.usage.cost"] == 0.06

    def test_agent_span_has_duration(self, tracing_setup):
        _run_with_events([INIT_EVENT, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        agent_span = next(s for s in spans if "invoke_agent" in s.name)
        attrs = _attrs(agent_span)
        assert attrs["subprocess.duration_ms"] == 12162

    def test_agent_span_ok_status_on_success(self, tracing_setup):
        _run_with_events([INIT_EVENT, RESULT_EVENT])
        spans = tracing_setup.get_finished_spans()
        agent_span = next(s for s in spans if "invoke_agent" in s.name)
        assert agent_span.status.status_code == StatusCode.OK

    def test_agent_span_error_status_on_failure(self, tracing_setup):
        _run_with_events([INIT_EVENT, RESULT_EVENT], returncode=1)
        spans = tracing_setup.get_finished_spans()
        agent_span = next(s for s in spans if "invoke_agent" in s.name)
        assert agent_span.status.status_code == StatusCode.ERROR

    def test_file_not_found_returns_exit_127(self, tracing_setup):
        with patch("factory_tracing.executor.subprocess.Popen", side_effect=FileNotFoundError):
            result = run_traced_agent("prompt", "role", "run-1", "proj")
        assert result.exit_code == 127

    def test_result_with_list_content(self, tracing_setup):
        result_event = {
            "type": "result",
            "subtype": "success",
            "result": [
                {"type": "text", "text": "Part one."},
                {"type": "text", "text": "Part two."},
            ],
            "usage": {"input_tokens": 50, "output_tokens": 25},
            "total_cost_usd": 0.01,
            "duration_ms": 1000,
            "num_turns": 1,
        }
        result = _run_with_events([INIT_EVENT, result_event])
        assert "Part one." in result.response_text
        assert "Part two." in result.response_text


class TestFullConversationFlow:
    def test_tool_use_then_text_response(self, tracing_setup):
        """Simulates: init -> tool_use -> tool_result -> text response -> result."""
        text_after_tool = {
            "type": "assistant",
            "message": {
                "model": "claude-opus-4-6[1m]",
                "content": [{"type": "text", "text": "Based on the file, here is my analysis."}],
                "usage": {"input_tokens": 150, "output_tokens": 60},
            },
        }
        events = [INIT_EVENT, ASSISTANT_TOOL_USE_EVENT, TOOL_RESULT_EVENT, text_after_tool, RESULT_EVENT]
        result = _run_with_events(events)

        spans = tracing_setup.get_finished_spans()
        tool_spans = [s for s in spans if s.name.startswith("tool:")]
        llm_spans = [s for s in spans if s.name == "llm_call"]
        agent_spans = [s for s in spans if "invoke_agent" in s.name]

        assert len(tool_spans) == 1
        assert len(llm_spans) == 1
        assert len(agent_spans) == 1
        assert result.response_text == "The file contains a hello function."

    def test_unclosed_tool_spans_get_error_status(self, tracing_setup):
        """Tool use with no matching result should get an error status."""
        events = [INIT_EVENT, ASSISTANT_TOOL_USE_EVENT, RESULT_EVENT]
        _run_with_events(events)
        spans = tracing_setup.get_finished_spans()
        tool_spans = [s for s in spans if s.name.startswith("tool:")]
        assert len(tool_spans) == 1
        assert tool_spans[0].status.status_code == StatusCode.ERROR
