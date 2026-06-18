"""Tests for executor module — ConversationTracker and stream-json parsing."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from factory_tracing.executor import (
    AgentResult,
    ConversationTracker,
    _process_assistant_message,
    _process_result_event,
    _process_tool_result,
)
from factory_tracing.provider import get_tracer
from factory_tracing.spans import clean_model_name


class TestConversationTracker:
    def test_init_with_system_prompt(self):
        tracker = ConversationTracker("You are helpful.", "Do something")
        assert len(tracker.messages) == 2
        assert tracker.messages[0] == {"role": "system", "content": "You are helpful."}
        assert tracker.messages[1] == {"role": "user", "content": "Do something"}

    def test_init_without_system_prompt(self):
        tracker = ConversationTracker(None, "Do something")
        assert len(tracker.messages) == 1
        assert tracker.messages[0] == {"role": "user", "content": "Do something"}

    def test_add_assistant(self):
        tracker = ConversationTracker(None, "task")
        blocks = [{"type": "text", "text": "Hello"}]
        tracker.add_assistant(blocks)
        assert len(tracker.messages) == 2
        assert tracker.messages[1]["role"] == "assistant"
        assert tracker.messages[1]["content"] == blocks

    def test_add_tool_result(self):
        tracker = ConversationTracker(None, "task")
        tracker.add_tool_result("tool_123", "result text")
        assert len(tracker.messages) == 2
        assert tracker.messages[1]["role"] == "user"
        content = tracker.messages[1]["content"]
        assert content[0]["type"] == "tool_result"
        assert content[0]["tool_use_id"] == "tool_123"
        assert content[0]["content"] == "result text"

    def test_get_messages_json_is_valid(self):
        tracker = ConversationTracker("system", "task")
        tracker.add_assistant([{"type": "text", "text": "response"}])
        result = tracker.get_messages_json()
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 3
        assert parsed[0]["role"] == "system"
        assert parsed[1]["role"] == "user"
        assert parsed[2]["role"] == "assistant"

    def test_get_messages_json_starts_with_bracket(self):
        tracker = ConversationTracker(None, "task")
        result = tracker.get_messages_json()
        assert result.startswith("[")


class TestProcessAssistantMessage:
    def test_creates_llm_call_span(self):
        tracer = get_tracer("test")
        with tracer.start_as_current_span("test-parent") as parent_span:
            conversation = ConversationTracker(None, "test task")
            result = AgentResult()
            message = {
                "content": [{"type": "text", "text": "Hello world"}],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_input_tokens": 10,
                    "cache_read_input_tokens": 20,
                },
            }
            _process_assistant_message(
                message, conversation, tracer, parent_span, "claude-sonnet-4-20250514", result,
            )
            assert result.input_tokens == 100
            assert result.output_tokens == 50
            assert result.cache_creation_tokens == 10
            assert result.cache_read_tokens == 20
            assert len(conversation.messages) == 2

    def test_creates_tool_span_for_tool_use(self):
        tracer = get_tracer("test")
        with tracer.start_as_current_span("test-parent") as parent_span:
            conversation = ConversationTracker(None, "test task")
            result = AgentResult()
            message = {
                "content": [
                    {"type": "tool_use", "id": "tool_1", "name": "Bash", "input": {"command": "ls"}},
                ],
                "usage": {"input_tokens": 50, "output_tokens": 25},
            }
            _process_assistant_message(
                message, conversation, tracer, parent_span, "claude-sonnet-4-20250514", result,
            )
            assert result.input_tokens == 50

    def test_accumulates_tokens_across_messages(self):
        tracer = get_tracer("test")
        with tracer.start_as_current_span("test-parent") as parent_span:
            conversation = ConversationTracker(None, "test task")
            result = AgentResult()
            for i in range(3):
                message = {
                    "content": [{"type": "text", "text": f"Turn {i}"}],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                }
                _process_assistant_message(
                    message, conversation, tracer, parent_span, "claude-sonnet-4-20250514", result,
                )
            assert result.input_tokens == 300
            assert result.output_tokens == 150


class TestProcessResultEvent:
    def test_sets_cost_and_model(self):
        tracer = get_tracer("test")
        with tracer.start_as_current_span("test-agent") as span:
            result = AgentResult()
            data = {
                "cost_usd": 0.05,
                "model": "claude-sonnet-4-20250514[1m]",
                "duration_ms": 5000,
                "num_turns": 3,
                "result": "Final answer",
                "usage": {"input_tokens": 500, "output_tokens": 200},
            }
            _process_result_event(data, result, span)
            assert result.cost_usd == 0.05
            assert result.model == "claude-sonnet-4-20250514"
            assert result.stdout == "Final answer"
            assert result.duration_ms == 5000
            assert result.num_turns == 3


class TestProcessToolResult:
    def test_adds_tool_result_to_conversation(self):
        tracer = get_tracer("test")
        with tracer.start_as_current_span("test-parent") as parent_span:
            conversation = ConversationTracker(None, "test")
            event = {
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "output text"},
                ],
            }
            _process_tool_result(event, conversation, tracer, parent_span)
            assert len(conversation.messages) == 2
            assert conversation.messages[1]["role"] == "user"


class TestCleanModelName:
    def test_strips_bracket_suffix(self):
        assert clean_model_name("claude-sonnet-4-20250514[1m]") == "claude-sonnet-4-20250514"

    def test_no_suffix(self):
        assert clean_model_name("claude-sonnet-4-20250514") == "claude-sonnet-4-20250514"

    def test_empty(self):
        assert clean_model_name("") == ""

    def test_multiple_brackets(self):
        assert clean_model_name("model[ext][1m]") == "model[ext]"
