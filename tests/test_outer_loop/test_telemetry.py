"""Tests for telemetry extraction from EvalResult."""

from __future__ import annotations

from factory.outer_loop.designer import extract_telemetry
from factory.outer_loop.models import EvalResult


class TestExtractTelemetry:
    def test_basic_fields(self) -> None:
        result = EvalResult(
            score=0.75,
            benchmark_score=0.8,
            hygiene_score=0.7,
            cost_usd=1.5,
            complexity=5.0,
        )
        telemetry = extract_telemetry(result)

        assert telemetry["benchmark_score"] == 0.8
        assert telemetry["hygiene_score"] == 0.7
        assert telemetry["cost_usd"] == 1.5
        assert telemetry["complexity"] == 5.0
        assert telemetry["score"] == 0.75

    def test_node_stats_from_details(self) -> None:
        result = EvalResult(
            score=0.5,
            details={
                "node_stats": {
                    "builder": {"failure_rate": 0.3, "tokens": 5000},
                    "researcher": {"failure_rate": 0.0, "tokens": 2000},
                },
            },
        )
        telemetry = extract_telemetry(result)

        node_stats = telemetry["node_stats"]
        assert isinstance(node_stats, dict)
        assert "builder" in node_stats
        assert "researcher" in node_stats

    def test_dominant_failure_from_details(self) -> None:
        result = EvalResult(
            score=0.3,
            details={"dominant_failure": "timeout"},
        )
        telemetry = extract_telemetry(result)

        assert telemetry["dominant_failure"] == "timeout"

    def test_empty_details(self) -> None:
        result = EvalResult(score=0.5)
        telemetry = extract_telemetry(result)

        assert telemetry["node_stats"] == {}
        assert telemetry["dominant_failure"] == ""

    def test_missing_node_stats(self) -> None:
        result = EvalResult(
            score=0.5,
            details={"some_other_key": "value"},
        )
        telemetry = extract_telemetry(result)

        assert telemetry["node_stats"] == {}
        assert telemetry["dominant_failure"] == ""

    def test_all_fields_present(self) -> None:
        result = EvalResult(
            score=0.6,
            benchmark_score=0.7,
            hygiene_score=0.5,
            cost_usd=2.0,
            complexity=8.0,
            details={
                "node_stats": {"gate": {"failure_rate": 0.1}},
                "dominant_failure": "crash",
            },
        )
        telemetry = extract_telemetry(result)

        expected_keys = {
            "node_stats", "dominant_failure", "benchmark_score",
            "hygiene_score", "cost_usd", "complexity", "score",
        }
        assert set(telemetry.keys()) == expected_keys
