"""Tests for factory.optimization.analyzer — StepRecord production."""

from __future__ import annotations

import json
from pathlib import Path

from factory.optimization.analyzer import StepAnalyzer


class TestStepAnalyzer:
    def test_empty_factory_dir(self, tmp_path: Path) -> None:
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        analyzer = StepAnalyzer(factory_dir)
        result = analyzer.latest()
        # CycleAnalyzer still returns a record for empty dirs (cycle_number=1)
        # StepAnalyzer wraps it, so it returns a StepRecord
        assert result is not None
        assert result.step_number == 1

    def test_all_steps_from_empty_dir(self, tmp_path: Path) -> None:
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        analyzer = StepAnalyzer(factory_dir)
        steps = analyzer.all_steps()
        # CycleAnalyzer.analyze() returns one record even for empty dirs
        assert len(steps) == 1

    def test_with_events(self, tmp_path: Path) -> None:
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()

        events = [
            {"type": "experiment.begin", "timestamp": "2026-01-01T00:00:00Z",
             "data": {"exp_id": 1}},
            {"type": "agent.started", "timestamp": "2026-01-01T00:00:01Z",
             "agent": "builder", "data": {}},
            {"type": "agent.completed", "timestamp": "2026-01-01T00:01:00Z",
             "agent": "builder", "data": {"total_cost_usd": 0.5}},
            {"type": "experiment.finalize", "timestamp": "2026-01-01T00:02:00Z",
             "data": {"exp_id": 1, "verdict": "keep"}},
        ]
        (factory_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events)
        )

        analyzer = StepAnalyzer(factory_dir)
        record = analyzer.latest()
        assert record is not None
        assert record.step_number == 1

    def test_summarize_verdict(self) -> None:
        assert StepAnalyzer._summarize_verdict(1, 0, 0) == "keep"
        assert StepAnalyzer._summarize_verdict(0, 1, 0) == "revert"
        assert StepAnalyzer._summarize_verdict(0, 0, 1) == "error"
        assert StepAnalyzer._summarize_verdict(0, 0, 0) is None
        assert StepAnalyzer._summarize_verdict(2, 1, 0) == "keep"
        assert StepAnalyzer._summarize_verdict(1, 2, 0) == "revert"
