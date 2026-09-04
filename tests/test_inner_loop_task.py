"""Tests for InnerLoop.step() with task-driven execution path."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from factory.cycle_analyzer import CycleRecord
from factory.inner_loop import InnerLoop
from factory.task import (
    ExitCodeScoring,
    TaskDefinition,
    TaskInstance,
    VerifyResult,
)


class TestStepWithoutTask:
    """task=None path is unchanged (backward compat)."""

    def test_step_returns_cycle_record(self, tmp_path: Path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()

        loop = InnerLoop(
            project_dir=tmp_path,
            mode="test",
        )
        assert loop.task is None

    def test_step_dispatches_to_subprocess(self, tmp_path: Path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()

        loop = InnerLoop(project_dir=tmp_path, mode="test")
        assert loop.task is None
        assert hasattr(loop, "_step_subprocess")


class TestStepWithTask:
    """task is set path — iterates instances, calls task.run(), aggregates."""

    def test_step_calls_task_run(self, tmp_path: Path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()

        task = MagicMock()
        task.instances.return_value = [TaskInstance(id="inst-1")]
        task.run.return_value = VerifyResult(passed=True, score=0.8)
        task.definition = TaskDefinition(name="mock", scoring=ExitCodeScoring())

        loop = InnerLoop(project_dir=tmp_path, mode="test", task=task)
        record = loop.step()

        assert isinstance(record, CycleRecord)
        assert record.score_end == 0.8
        task.run.assert_called_once()
        assert record.instance_results is not None
        assert len(record.instance_results) == 1
        assert record.instance_results[0]["instance_id"] == "inst-1"
        assert record.instance_results[0]["score"] == 0.8

    def test_step_aggregates_mean(self, tmp_path: Path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()

        task = MagicMock()
        task.instances.return_value = [
            TaskInstance(id="a"),
            TaskInstance(id="b"),
            TaskInstance(id="c"),
        ]
        task.run.side_effect = [
            VerifyResult(passed=True, score=1.0),
            VerifyResult(passed=True, score=0.5),
            VerifyResult(passed=False, score=0.0),
        ]
        task.definition = TaskDefinition(name="mock", scoring=ExitCodeScoring())

        loop = InnerLoop(project_dir=tmp_path, mode="test", task=task)
        record = loop.step()

        assert record.score_end == pytest.approx(0.5)
        assert record.instance_results is not None
        assert len(record.instance_results) == 3

    def test_step_handles_exception_in_task_run(self, tmp_path: Path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()

        task = MagicMock()
        task.instances.return_value = [TaskInstance(id="fail")]
        task.run.side_effect = RuntimeError("boom")
        task.definition = TaskDefinition(name="mock", scoring=ExitCodeScoring())

        loop = InnerLoop(project_dir=tmp_path, mode="test", task=task)
        record = loop.step()

        assert record.score_end == 0.0
        assert record.instance_results is not None
        assert record.instance_results[0]["error"] == "boom"

    def test_step_increments_step_count(self, tmp_path: Path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()

        task = MagicMock()
        task.instances.return_value = [TaskInstance(id="x")]
        task.run.return_value = VerifyResult(passed=True, score=1.0)
        task.definition = TaskDefinition(name="mock", scoring=ExitCodeScoring())

        loop = InnerLoop(project_dir=tmp_path, mode="test", task=task)
        r1 = loop.step()
        r2 = loop.step()

        assert r1.cycle_number == 1
        assert r2.cycle_number == 2
        assert len(loop.history()) == 2

    def test_step_with_no_instances(self, tmp_path: Path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()

        task = MagicMock()
        task.instances.return_value = []
        task.definition = TaskDefinition(name="mock", scoring=ExitCodeScoring())

        loop = InnerLoop(project_dir=tmp_path, mode="test", task=task)
        record = loop.step()

        assert record.score_end == 0.0
        assert record.instance_results == []


class TestStepAggregatesMethods:
    """Test non-default aggregation methods in _step_with_task (lines 300-320)."""

    def _make_loop_with_aggregate(self, tmp_path: Path, aggregate_method: str, scores: list[float]):
        """Helper: create InnerLoop with mocked task and patched aggregate."""
        from unittest.mock import patch

        from factory.models import AggregateMethod, InnerLoopConfig

        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir(exist_ok=True)

        task = MagicMock()
        instances = [TaskInstance(id=f"i{i}") for i in range(len(scores))]
        task.instances.return_value = instances
        task.run.side_effect = [
            VerifyResult(passed=s >= 0.5, score=s) for s in scores
        ]
        task.definition = TaskDefinition(name="mock", scoring=ExitCodeScoring())

        loop = InnerLoop(project_dir=tmp_path, mode="test", task=task)

        with patch("factory.models.InnerLoopConfig") as mock_config_cls:
            mock_config = MagicMock(spec=InnerLoopConfig)
            mock_config.aggregate = AggregateMethod(aggregate_method)
            mock_config_cls.return_value = mock_config
            record = loop.step()

        return record

    def test_step_aggregates_median(self, tmp_path: Path):
        """AggregateMethod.median computes the median of scores."""
        record = self._make_loop_with_aggregate(
            tmp_path, "median", [0.2, 0.5, 0.9]
        )
        assert record.score_end == pytest.approx(0.5)

    def test_step_aggregates_median_even(self, tmp_path: Path):
        """Median with even number of scores averages the two middle values."""
        record = self._make_loop_with_aggregate(
            tmp_path, "median", [0.0, 0.4, 0.6, 1.0]
        )
        assert record.score_end == pytest.approx(0.5)

    def test_step_aggregates_max(self, tmp_path: Path):
        """AggregateMethod.max returns the highest score."""
        record = self._make_loop_with_aggregate(
            tmp_path, "max", [0.1, 0.3, 0.9]
        )
        assert record.score_end == pytest.approx(0.9)

    def test_step_aggregates_max_single(self, tmp_path: Path):
        """Max with a single score returns that score."""
        record = self._make_loop_with_aggregate(
            tmp_path, "max", [0.42]
        )
        assert record.score_end == pytest.approx(0.42)

    def test_step_aggregates_all_pass_true(self, tmp_path: Path):
        """AggregateMethod.all_pass returns 1.0 when all scores >= 1.0."""
        record = self._make_loop_with_aggregate(
            tmp_path, "all_pass", [1.0, 1.0, 1.0]
        )
        assert record.score_end == pytest.approx(1.0)

    def test_step_aggregates_all_pass_false(self, tmp_path: Path):
        """AggregateMethod.all_pass returns 0.0 when any score < 1.0."""
        record = self._make_loop_with_aggregate(
            tmp_path, "all_pass", [1.0, 0.9, 1.0]
        )
        assert record.score_end == pytest.approx(0.0)

    def test_step_aggregates_all_pass_empty(self, tmp_path: Path):
        """Empty scores with any aggregate returns 0.0."""
        result = self._make_loop_with_aggregate(tmp_path, "all_pass", [])
        assert result.score_end == pytest.approx(0.0)


class TestCycleRecordInstanceResults:
    """CycleRecord.instance_results field."""

    def test_default_none(self):
        record = CycleRecord(
            cycle_number=0,
            mode="test",
            started_at=None,
            ended_at=None,
            duration_s=0,
            score_start=None,
            score_end=None,
            score_delta=None,
        )
        assert record.instance_results is None

    def test_set_to_list(self):
        record = CycleRecord(
            cycle_number=0,
            mode="test",
            started_at=None,
            ended_at=None,
            duration_s=0,
            score_start=None,
            score_end=None,
            score_delta=None,
            instance_results=[{"instance_id": "a", "score": 1.0}],
        )
        assert record.instance_results is not None
        assert len(record.instance_results) == 1
