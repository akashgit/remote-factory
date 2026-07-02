"""Tests for factory.spec._json_util — JSON extraction from agent stdout."""

from __future__ import annotations

import pytest

from factory.spec._json_util import extract_json


class TestExtractJsonObject:
    def test_plain_object(self) -> None:
        result = extract_json('{"errors": [], "warnings": ["x"]}')
        assert result == {"errors": [], "warnings": ["x"]}

    def test_object_with_surrounding_text(self) -> None:
        result = extract_json('Here is the result:\n{"a": 1}\nDone.')
        assert result == {"a": 1}

    def test_object_in_code_fence(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        result = extract_json(text)
        assert result == {"key": "value"}

    def test_nested_object(self) -> None:
        text = '{"outer": {"inner": [1, 2, 3]}}'
        result = extract_json(text)
        assert result == {"outer": {"inner": [1, 2, 3]}}


class TestExtractJsonArray:
    def test_plain_array(self) -> None:
        result = extract_json('[{"type": "phantom"}]')
        assert result == [{"type": "phantom"}]

    def test_empty_array(self) -> None:
        result = extract_json("[]")
        assert result == []

    def test_array_in_code_fence(self) -> None:
        text = "```json\n[1, 2, 3]\n```"
        result = extract_json(text)
        assert result == [1, 2, 3]


class TestExtractJsonErrors:
    def test_no_json_raises(self) -> None:
        with pytest.raises(ValueError, match="No valid JSON found"):
            extract_json("This is just plain text with no JSON.")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="No valid JSON found"):
            extract_json("")

    def test_malformed_json_raises(self) -> None:
        with pytest.raises(ValueError, match="No valid JSON found"):
            extract_json("{broken: json}")


class TestExtractJsonEdgeCases:
    def test_whitespace_only(self) -> None:
        with pytest.raises(ValueError):
            extract_json("   \n\n   ")

    def test_code_fence_without_json_label(self) -> None:
        text = '```\n{"x": 1}\n```'
        result = extract_json(text)
        assert result == {"x": 1}

    def test_object_preferred_over_array_when_both_present(self) -> None:
        text = '{"obj": true} and also [1, 2]'
        result = extract_json(text)
        assert result == {"obj": True}
