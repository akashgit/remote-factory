"""Tests for factory.research.parser — result file parsing."""

import json
from pathlib import Path

import pytest

from factory.models import ResultParseError
from factory.research.runner import parse_result


@pytest.fixture
def result_file(tmp_path: Path) -> Path:
    """Create a result JSON file and return its path."""
    path = tmp_path / "results.json"
    path.write_text(json.dumps({
        "accuracy": 0.95,
        "results": {"accuracy": 0.92, "f1": 0.88},
        "resolved": 42,
        "total": 100,
        "nested": {"deep": {"value": 3.14}},
    }))
    return path


class TestParseResultSimpleKey:
    def test_top_level_key(self, result_file: Path) -> None:
        value = parse_result(result_file, "json", "accuracy")
        assert value == 0.95

    def test_integer_value_returned_as_float(self, result_file: Path) -> None:
        value = parse_result(result_file, "json", "resolved")
        assert value == 42.0
        assert isinstance(value, float)


class TestParseResultDottedPath:
    def test_nested_dotted_path(self, result_file: Path) -> None:
        value = parse_result(result_file, "json", "results.accuracy")
        assert value == 0.92

    def test_deep_nested_path(self, result_file: Path) -> None:
        value = parse_result(result_file, "json", "nested.deep.value")
        assert value == 3.14


class TestParseResultSlashRatio:
    def test_ratio_computation(self, result_file: Path) -> None:
        value = parse_result(result_file, "json", "resolved/total")
        assert value == pytest.approx(0.42)

    def test_zero_denominator(self, tmp_path: Path) -> None:
        path = tmp_path / "zero.json"
        path.write_text(json.dumps({"a": 5, "b": 0}))
        with pytest.raises(ResultParseError, match="zero"):
            parse_result(path, "json", "a/b")


class TestParseResultErrors:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ResultParseError, match="not found"):
            parse_result(tmp_path / "nope.json", "json", "x")

    def test_invalid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json")
        with pytest.raises(ResultParseError, match="invalid JSON"):
            parse_result(path, "json", "x")

    def test_missing_key(self, result_file: Path) -> None:
        with pytest.raises(ResultParseError, match="not found"):
            parse_result(result_file, "json", "nonexistent")

    def test_missing_nested_key(self, result_file: Path) -> None:
        with pytest.raises(ResultParseError, match="not found"):
            parse_result(result_file, "json", "results.missing")

    def test_non_numeric_value(self, tmp_path: Path) -> None:
        path = tmp_path / "str.json"
        path.write_text(json.dumps({"val": "not_a_number"}))
        with pytest.raises(ResultParseError, match="not numeric"):
            parse_result(path, "json", "val")

    def test_unsupported_parser(self, result_file: Path) -> None:
        with pytest.raises(ResultParseError, match="unsupported parser"):
            parse_result(result_file, "csv", "x")
