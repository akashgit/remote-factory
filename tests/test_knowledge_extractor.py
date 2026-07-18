"""Tests for factory.knowledge.extractor — deterministic and LLM extraction."""

import json


from factory.knowledge.extractor import (
    build_extraction_prompt,
    extract_from_tool_calls,
    parse_extraction_response,
)
from factory.knowledge.models import EntityType, PredicateType


# ── deterministic extraction ─────────────────────────────────────


class TestExtractFromToolCalls:
    def test_single_successful_call(self):
        calls = [{"name": "get_order", "result": '{"id": 42}', "success": True}]
        result = extract_from_tool_calls(calls, "process order")
        triplets = result.triplets
        call_triplets = [t for t in triplets if t.predicate == PredicateType.CALLS]
        assert len(call_triplets) == 1
        assert call_triplets[0].object.name == "get_order"
        success = [t for t in triplets if t.predicate == PredicateType.SUCCEEDS_AT]
        assert len(success) == 1

    def test_failed_call(self):
        calls = [{"name": "cancel_booking", "error": "booking not found"}]
        result = extract_from_tool_calls(calls, "cancel order")
        triplets = result.triplets
        failures = [t for t in triplets if t.predicate == PredicateType.FAILS_WITH]
        assert len(failures) == 1
        assert "booking not found" in failures[0].evidence
        task_fail = [t for t in triplets if t.predicate == PredicateType.FAILS_AT]
        assert len(task_fail) == 1

    def test_sequential_calls_produce_precedes(self):
        calls = [
            {"name": "search", "result": "found", "success": True},
            {"name": "update", "result": "ok", "success": True},
        ]
        result = extract_from_tool_calls(calls, "update item")
        precedes = [t for t in result.triplets if t.predicate == PredicateType.PRECEDES]
        assert len(precedes) == 1

    def test_mixed_success_and_failure(self):
        calls = [
            {"name": "get_user", "result": '{"name": "alice"}', "success": True},
            {"name": "delete_account", "error": "permission denied"},
        ]
        result = extract_from_tool_calls(calls, "delete user")
        task_fail = [t for t in result.triplets if t.predicate == PredicateType.FAILS_AT]
        assert len(task_fail) == 1

    def test_empty_calls(self):
        result = extract_from_tool_calls([], "empty task")
        success = [t for t in result.triplets if t.predicate == PredicateType.SUCCEEDS_AT]
        assert len(success) == 1

    def test_custom_agent_name(self):
        calls = [{"name": "tool_x", "success": True}]
        result = extract_from_tool_calls(calls, "task", agent_name="my_bot")
        agent_triplets = [t for t in result.triplets if t.subject.type == EntityType.AGENT]
        assert all(t.subject.name == "my_bot" for t in agent_triplets)

    def test_source_label(self):
        calls = [{"name": "tool_x", "success": True}]
        result = extract_from_tool_calls(calls, "task", source_label="run_005")
        assert result.source_label == "run_005"
        assert all(t.source == "run_005" for t in result.triplets)


# ── LLM response parsing ────────────────────────────────────────


VALID_JSON_RESPONSE = json.dumps(
    [
        {
            "subject": {"id": "agent:main", "type": "agent", "name": "Main Agent"},
            "predicate": "calls",
            "object": {"id": "tool:search", "type": "tool", "name": "Search"},
            "confidence": 0.95,
            "evidence": "Agent called search tool",
        },
        {
            "subject": {"id": "tool:search", "type": "tool", "name": "Search"},
            "predicate": "fails_with",
            "object": {"id": "error:timeout", "type": "error", "name": "Timeout"},
            "confidence": 0.8,
            "evidence": "Search timed out after 30s",
        },
    ]
)


class TestParseExtractionResponse:
    def test_valid_json(self):
        triplets = parse_extraction_response(VALID_JSON_RESPONSE)
        assert len(triplets) == 2
        assert triplets[0].subject.id == "agent:main"
        assert triplets[0].predicate == PredicateType.CALLS
        assert triplets[1].predicate == PredicateType.FAILS_WITH

    def test_json_in_code_fence(self):
        fenced = f"Here are the triplets:\n```json\n{VALID_JSON_RESPONSE}\n```"
        triplets = parse_extraction_response(fenced)
        assert len(triplets) == 2

    def test_unknown_predicate_falls_back(self):
        data = json.dumps(
            [
                {
                    "subject": {"id": "agent:x", "type": "agent", "name": "X"},
                    "predicate": "unknown_pred",
                    "object": {"id": "tool:y", "type": "tool", "name": "Y"},
                }
            ]
        )
        triplets = parse_extraction_response(data)
        assert len(triplets) == 1
        assert triplets[0].predicate == PredicateType.RELATED_TO

    def test_unknown_entity_type_falls_back(self):
        data = json.dumps(
            [
                {
                    "subject": {"id": "widget:x", "type": "widget", "name": "X"},
                    "predicate": "calls",
                    "object": {"id": "tool:y", "type": "tool", "name": "Y"},
                }
            ]
        )
        triplets = parse_extraction_response(data)
        assert len(triplets) == 1
        assert triplets[0].subject.type == EntityType.CONCEPT

    def test_invalid_json(self):
        triplets = parse_extraction_response("not json at all {{{}}")
        assert triplets == []

    def test_missing_fields_skipped(self):
        data = json.dumps(
            [
                {"subject": {"id": "a:b", "type": "agent", "name": "A"}, "predicate": "calls"},
                {
                    "subject": {"id": "a:b", "type": "agent", "name": "A"},
                    "predicate": "calls",
                    "object": {"id": "t:c", "type": "tool", "name": "C"},
                },
            ]
        )
        triplets = parse_extraction_response(data)
        assert len(triplets) == 1

    def test_confidence_clamped(self):
        data = json.dumps(
            [
                {
                    "subject": {"id": "a:x", "type": "agent", "name": "X"},
                    "predicate": "calls",
                    "object": {"id": "t:y", "type": "tool", "name": "Y"},
                    "confidence": 5.0,
                }
            ]
        )
        triplets = parse_extraction_response(data)
        assert len(triplets) == 1
        assert triplets[0].confidence == 1.0

    def test_source_label_applied(self):
        triplets = parse_extraction_response(VALID_JSON_RESPONSE, source_label="run_7")
        assert all(t.source == "run_7" for t in triplets)


# ── prompt building ──────────────────────────────────────────────


class TestBuildExtractionPrompt:
    def test_includes_entity_types(self):
        prompt = build_extraction_prompt("log data", "test task")
        for et in EntityType:
            assert et.value in prompt

    def test_includes_predicate_types(self):
        prompt = build_extraction_prompt("log data", "test task")
        for pt in PredicateType:
            assert pt.value in prompt

    def test_includes_task_context(self):
        prompt = build_extraction_prompt("log data", "cancel flight booking")
        assert "cancel flight booking" in prompt

    def test_truncates_long_content(self):
        long_content = "x" * 20000
        prompt = build_extraction_prompt(long_content, "task")
        assert len(prompt) < 20000 + 2000

    def test_max_triplets(self):
        prompt = build_extraction_prompt("log", "task", max_triplets=10)
        assert "10" in prompt
