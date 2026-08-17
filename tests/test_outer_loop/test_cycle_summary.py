"""Tests for cycle_summary.json writing (InnerLoop) and reading (SwarmEvaluator)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.inner_loop import InnerLoop
from factory.outer_loop.evaluator import SwarmEvaluator


@pytest.fixture()
def factory_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".factory"
    d.mkdir()
    return d


@pytest.fixture()
def loop(tmp_path: Path, factory_dir: Path) -> InnerLoop:
    return InnerLoop(project_dir=tmp_path, mode="evolve-test")


def _write_events(factory_dir: Path, events: list[dict]) -> None:
    lines = [json.dumps(e) for e in events]
    (factory_dir / "events.jsonl").write_text("\n".join(lines) + "\n")


class TestWriteCycleSummary:
    def test_creates_summary_file(self, loop: InnerLoop, factory_dir: Path) -> None:
        path = loop._write_cycle_summary(
            returncode=0, event_offset=0, duration_ms=5000,
            builder_committed=True, experiments=1,
        )
        assert path.exists()
        assert path.name == "cycle_summary.json"
        assert "evolve-test" in str(path)

    def test_summary_structure(self, loop: InnerLoop, factory_dir: Path) -> None:
        loop._write_cycle_summary(
            returncode=0, event_offset=0, duration_ms=12345,
            builder_committed=False, experiments=2,
        )
        summary_path = (
            factory_dir / "outer_loop" / "runs" / "evolve-test" / "cycle_summary.json"
        )
        data = json.loads(summary_path.read_text())
        assert data["mode"] == "evolve-test"
        assert data["duration_ms"] == 12345
        assert data["experiments"] == 2
        assert isinstance(data["score"], float)
        assert isinstance(data["errors"], list)

    def test_perfect_score(self, loop: InnerLoop, factory_dir: Path) -> None:
        _write_events(factory_dir, [
            {"type": "agent.started", "timestamp": "2026-01-01T00:00:00", "agent": "builder"},
            {"type": "agent.completed", "timestamp": "2026-01-01T00:01:00", "agent": "builder",
             "data": {"total_cost_usd": 1.5}},
        ])
        loop._write_cycle_summary(
            returncode=0, event_offset=0, duration_ms=60000,
            builder_committed=True, experiments=1,
        )
        summary_path = (
            factory_dir / "outer_loop" / "runs" / "evolve-test" / "cycle_summary.json"
        )
        data = json.loads(summary_path.read_text())
        assert data["score"] == 1.0
        assert data["agents_spawned"] == 1
        assert data["agents_succeeded"] == 1
        assert data["agents_failed"] == 0
        assert data["builder_committed"] is True
        assert data["tests_passed"] is True
        assert data["cost_usd"] == 1.5

    def test_no_agents_score_zero(self, loop: InnerLoop, factory_dir: Path) -> None:
        loop._write_cycle_summary(
            returncode=1, event_offset=0, duration_ms=100,
            builder_committed=False, experiments=0,
        )
        summary_path = (
            factory_dir / "outer_loop" / "runs" / "evolve-test" / "cycle_summary.json"
        )
        data = json.loads(summary_path.read_text())
        assert data["score"] == 0.0
        assert data["errors"] == ["subprocess exited with code 1"]

    def test_partial_score_with_failures(
        self, loop: InnerLoop, factory_dir: Path,
    ) -> None:
        _write_events(factory_dir, [
            {"type": "agent.started", "timestamp": "2026-01-01T00:00:00", "agent": "researcher"},
            {"type": "agent.completed", "timestamp": "2026-01-01T00:01:00", "agent": "researcher",
             "data": {"total_cost_usd": 0.5}},
            {"type": "agent.started", "timestamp": "2026-01-01T00:01:00", "agent": "builder"},
            {"type": "agent.failed", "timestamp": "2026-01-01T00:02:00", "agent": "builder",
             "data": {"error": "timeout"}},
        ])
        loop._write_cycle_summary(
            returncode=1, event_offset=0, duration_ms=120000,
            builder_committed=False, experiments=0,
        )
        summary_path = (
            factory_dir / "outer_loop" / "runs" / "evolve-test" / "cycle_summary.json"
        )
        data = json.loads(summary_path.read_text())
        # agents spawned (+0.2), but failures and bad returncode
        assert data["score"] == 0.2
        assert data["agents_spawned"] == 2
        assert data["agents_succeeded"] == 1
        assert data["agents_failed"] == 1

    def test_event_offset_skips_earlier_events(
        self, loop: InnerLoop, factory_dir: Path,
    ) -> None:
        _write_events(factory_dir, [
            {"type": "agent.started", "timestamp": "2026-01-01T00:00:00", "agent": "old"},
            {"type": "agent.completed", "timestamp": "2026-01-01T00:01:00", "agent": "old",
             "data": {"total_cost_usd": 10.0}},
            {"type": "agent.started", "timestamp": "2026-01-01T00:02:00", "agent": "new"},
            {"type": "agent.completed", "timestamp": "2026-01-01T00:03:00", "agent": "new",
             "data": {"total_cost_usd": 2.0}},
        ])
        loop._write_cycle_summary(
            returncode=0, event_offset=2, duration_ms=60000,
            builder_committed=True, experiments=0,
        )
        summary_path = (
            factory_dir / "outer_loop" / "runs" / "evolve-test" / "cycle_summary.json"
        )
        data = json.loads(summary_path.read_text())
        assert data["agents_spawned"] == 1
        assert data["cost_usd"] == 2.0


class TestHeuristicScoreWeights:
    """Verify each heuristic signal contributes exactly 0.2, no double-counting."""

    def _score(self, loop: InnerLoop, factory_dir: Path, **kwargs: object) -> float:
        defaults: dict[str, object] = {
            "returncode": 1, "event_offset": 0, "duration_ms": 100,
            "builder_committed": False, "experiments": 0,
        }
        defaults.update(kwargs)
        loop._write_cycle_summary(**defaults)  # type: ignore[arg-type]
        summary_path = (
            factory_dir / "outer_loop" / "runs" / "evolve-test" / "cycle_summary.json"
        )
        return json.loads(summary_path.read_text())["heuristic_score"]

    def test_signal_agents_spawned(self, loop: InnerLoop, factory_dir: Path) -> None:
        _write_events(factory_dir, [
            {"type": "agent.started", "agent": "x"},
            {"type": "agent.failed", "agent": "x", "data": {}},
        ])
        assert self._score(loop, factory_dir) == 0.2

    def test_signal_builder_committed(self, loop: InnerLoop, factory_dir: Path) -> None:
        assert self._score(loop, factory_dir, builder_committed=True) == 0.2

    def test_signal_returncode_zero(self, loop: InnerLoop, factory_dir: Path) -> None:
        assert self._score(loop, factory_dir, returncode=0) == 0.2

    def test_signal_no_failures(self, loop: InnerLoop, factory_dir: Path) -> None:
        _write_events(factory_dir, [
            {"type": "agent.started", "agent": "x"},
            {"type": "agent.completed", "agent": "x", "data": {"total_cost_usd": 0}},
        ])
        assert self._score(loop, factory_dir) == 0.4  # agents_spawned + no_failures

    def test_signal_experiments_recorded(self, loop: InnerLoop, factory_dir: Path) -> None:
        assert self._score(loop, factory_dir, experiments=1) == 0.2

    def test_all_signals_sum_to_one(self, loop: InnerLoop, factory_dir: Path) -> None:
        _write_events(factory_dir, [
            {"type": "agent.started", "agent": "b"},
            {"type": "agent.completed", "agent": "b", "data": {"total_cost_usd": 0}},
        ])
        score = self._score(
            loop, factory_dir, returncode=0, builder_committed=True, experiments=1,
        )
        assert score == 1.0

    def test_no_double_counting_returncode(self, loop: InnerLoop, factory_dir: Path) -> None:
        score = self._score(loop, factory_dir, returncode=0, experiments=0)
        assert score == 0.2  # returncode contributes exactly once


class TestReadCycleSummaryScore:
    def test_reads_existing_summary(self, tmp_path: Path) -> None:
        summary_dir = tmp_path / ".factory" / "outer_loop" / "runs" / "evolve-x"
        summary_dir.mkdir(parents=True)
        (summary_dir / "cycle_summary.json").write_text(
            json.dumps({"score": 0.8})
        )
        result = SwarmEvaluator._read_cycle_summary_score(tmp_path, "evolve-x")
        assert result == 0.8

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        result = SwarmEvaluator._read_cycle_summary_score(tmp_path, "missing")
        assert result is None

    def test_returns_none_for_invalid_json(self, tmp_path: Path) -> None:
        summary_dir = tmp_path / ".factory" / "outer_loop" / "runs" / "bad"
        summary_dir.mkdir(parents=True)
        (summary_dir / "cycle_summary.json").write_text("not json")
        result = SwarmEvaluator._read_cycle_summary_score(tmp_path, "bad")
        assert result is None

    def test_returns_zero_for_missing_score_key(self, tmp_path: Path) -> None:
        summary_dir = tmp_path / ".factory" / "outer_loop" / "runs" / "no-score"
        summary_dir.mkdir(parents=True)
        (summary_dir / "cycle_summary.json").write_text(json.dumps({"mode": "x"}))
        result = SwarmEvaluator._read_cycle_summary_score(tmp_path, "no-score")
        assert result == 0.0
