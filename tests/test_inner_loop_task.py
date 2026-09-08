"""Tests for InnerLoop.step() with task-driven execution path."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from factory.cycle_analyzer import CycleRecord
from factory.inner_loop import InnerLoop
from factory.task import (
    ScoringContract,
    TaskDefinition,
    TaskInstance,
    VerifyResult,
)
from factory.workflow.primitives import AgentNode, AgentRole, Workflow


def _make_workflow(name: str = "test") -> Workflow:
    return Workflow(
        name=name,
        nodes={
            "builder": AgentNode(
                id="builder",
                role=AgentRole.BUILDER,
                prompt_template="build {project_path}",
            ),
        },
        edges=[],
        start_node="builder",
    )


def _make_exec_result(success: bool = True) -> MagicMock:
    r = MagicMock()
    r.success = success
    r.halted = not success
    r.halt_reason = "" if success else "halted"
    r.nodes_executed = 1
    r.duration_ms = 100.0
    return r


import asyncio


def _async_return(val: object) -> MagicMock:
    """Create a MagicMock that returns a coroutine yielding val."""
    async def _coro(*a: object, **kw: object) -> object:
        return val
    m = MagicMock(side_effect=_coro)
    return m


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
    """task is set path — setup → WorkflowExecutor → verify per instance."""

    def test_step_calls_setup_prompt_verify(self, tmp_path: Path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()

        call_order: list[str] = []
        task = MagicMock()
        task.instances.return_value = [TaskInstance(id="inst-1")]
        task.definition = TaskDefinition(name="mock", scoring=ScoringContract(method="exit_code"))

        def track_setup(inst, ws):
            call_order.append("setup")

        def track_prompt(inst):
            call_order.append("prompt")
            return "test prompt"

        def track_verify(inst, ws):
            call_order.append("verify")
            return VerifyResult(passed=True, score=0.8)

        task.setup.side_effect = track_setup
        task.prompt.side_effect = track_prompt
        task.verify.side_effect = track_verify

        wf = _make_workflow()

        with patch("factory.workflow.executor.WorkflowExecutor") as MockExecutor:
            mock_exec = MagicMock()
            mock_exec.execute = _async_return(_make_exec_result())
            MockExecutor.return_value = mock_exec

            loop = InnerLoop(project_dir=tmp_path, mode="test", task=task, workflow=wf)
            record = loop.step()

        assert call_order == ["setup", "prompt", "verify"]
        assert isinstance(record, CycleRecord)
        assert record.score_end == 0.8
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
        task.setup.return_value = None
        task.prompt.return_value = "prompt"
        task.verify.side_effect = [
            VerifyResult(passed=True, score=1.0),
            VerifyResult(passed=True, score=0.5),
            VerifyResult(passed=False, score=0.0),
        ]
        task.definition = TaskDefinition(name="mock", scoring=ScoringContract(method="exit_code"))

        wf = _make_workflow()

        with patch("factory.workflow.executor.WorkflowExecutor") as MockExecutor:
            mock_exec = MagicMock()
            mock_exec.execute = _async_return(_make_exec_result())
            MockExecutor.return_value = mock_exec

            loop = InnerLoop(project_dir=tmp_path, mode="test", task=task, workflow=wf)
            record = loop.step()

        assert record.score_end == pytest.approx(0.5)
        assert record.instance_results is not None
        assert len(record.instance_results) == 3

    def test_step_handles_exception_in_setup(self, tmp_path: Path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()

        task = MagicMock()
        task.instances.return_value = [TaskInstance(id="fail")]
        task.setup.side_effect = RuntimeError("boom")
        task.definition = TaskDefinition(name="mock", scoring=ScoringContract(method="exit_code"))

        wf = _make_workflow()
        loop = InnerLoop(project_dir=tmp_path, mode="test", task=task, workflow=wf)
        record = loop.step()

        assert record.score_end == 0.0
        assert record.instance_results is not None
        assert record.instance_results[0]["error"] == "boom"

    def test_step_increments_step_count(self, tmp_path: Path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()

        task = MagicMock()
        task.instances.return_value = [TaskInstance(id="x")]
        task.setup.return_value = None
        task.prompt.return_value = "p"
        task.verify.return_value = VerifyResult(passed=True, score=1.0)
        task.definition = TaskDefinition(name="mock", scoring=ScoringContract(method="exit_code"))

        wf = _make_workflow()

        with patch("factory.workflow.executor.WorkflowExecutor") as MockExecutor:
            mock_exec = MagicMock()
            mock_exec.execute = _async_return(_make_exec_result())
            MockExecutor.return_value = mock_exec

            loop = InnerLoop(project_dir=tmp_path, mode="test", task=task, workflow=wf)
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
        task.definition = TaskDefinition(name="mock", scoring=ScoringContract(method="exit_code"))

        wf = _make_workflow()
        loop = InnerLoop(project_dir=tmp_path, mode="test", task=task, workflow=wf)
        record = loop.step()

        assert record.score_end == 0.0
        assert record.instance_results == []

    def test_step_passes_prompt_as_initial_context(self, tmp_path: Path):
        """WorkflowExecutor receives task.prompt() as initial_context."""
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()

        task = MagicMock()
        task.instances.return_value = [TaskInstance(id="ctx")]
        task.setup.return_value = None
        task.prompt.return_value = "domain-specific prompt text"
        task.verify.return_value = VerifyResult(passed=True, score=1.0)
        task.definition = TaskDefinition(name="mock", scoring=ScoringContract(method="exit_code"))

        wf = _make_workflow()

        with patch("factory.workflow.executor.WorkflowExecutor") as MockExecutor:
            mock_exec = MagicMock()
            mock_exec.execute = _async_return(_make_exec_result())
            MockExecutor.return_value = mock_exec

            loop = InnerLoop(project_dir=tmp_path, mode="test", task=task, workflow=wf)
            loop.step()

        MockExecutor.assert_called_once()
        call_kwargs = MockExecutor.call_args
        assert call_kwargs.kwargs["initial_context"] == "domain-specific prompt text"

    def test_step_attaches_executor_details(self, tmp_path: Path):
        """verify result details include executor metadata."""
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()

        task = MagicMock()
        task.instances.return_value = [TaskInstance(id="det")]
        task.setup.return_value = None
        task.prompt.return_value = "p"
        task.verify.return_value = VerifyResult(passed=True, score=0.9, details={"custom": "val"})
        task.definition = TaskDefinition(name="mock", scoring=ScoringContract(method="exit_code"))

        wf = _make_workflow()

        with patch("factory.workflow.executor.WorkflowExecutor") as MockExecutor:
            mock_exec = MagicMock()
            mock_exec.execute = _async_return(_make_exec_result(success=True))
            MockExecutor.return_value = mock_exec

            loop = InnerLoop(project_dir=tmp_path, mode="test", task=task, workflow=wf)
            record = loop.step()

        ir = record.instance_results[0]
        assert ir["details"]["custom"] == "val"
        assert ir["details"]["executor_success"] is True
        assert ir["details"]["executor_nodes_executed"] == 1
        assert "executor_duration_ms" in ir["details"]

    def test_step_verify_called_even_on_executor_halt(self, tmp_path: Path):
        """verify() is always called, even when the executor halts."""
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()

        verify_called = {"called": False}

        task = MagicMock()
        task.instances.return_value = [TaskInstance(id="halt")]
        task.setup.return_value = None
        task.prompt.return_value = "p"

        def track_verify(inst, ws):
            verify_called["called"] = True
            return VerifyResult(passed=False, score=0.0)

        task.verify.side_effect = track_verify
        task.definition = TaskDefinition(name="mock", scoring=ScoringContract(method="exit_code"))

        wf = _make_workflow()

        with patch("factory.workflow.executor.WorkflowExecutor") as MockExecutor:
            mock_exec = MagicMock()
            mock_exec.execute = _async_return(_make_exec_result(success=False))
            MockExecutor.return_value = mock_exec

            loop = InnerLoop(project_dir=tmp_path, mode="test", task=task, workflow=wf)
            record = loop.step()

        assert verify_called["called"]
        assert record.instance_results[0]["details"]["executor_halt_reason"] == "halted"


class TestStepAggregatesMethods:
    """Test non-default aggregation methods in _step_with_task."""

    def _make_loop_with_aggregate(self, tmp_path: Path, aggregate_method: str, scores: list[float]):
        from factory.models import AggregateMethod, InnerLoopConfig

        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir(exist_ok=True)

        task = MagicMock()
        instances = [TaskInstance(id=f"i{i}") for i in range(len(scores))]
        task.instances.return_value = instances
        task.setup.return_value = None
        task.prompt.return_value = "p"
        task.verify.side_effect = [
            VerifyResult(passed=s >= 0.5, score=s) for s in scores
        ]
        task.definition = TaskDefinition(name="mock", scoring=ScoringContract(method="exit_code"))

        wf = _make_workflow()

        with patch("factory.workflow.executor.WorkflowExecutor") as MockExecutor:
            mock_exec = MagicMock()
            mock_exec.execute = _async_return(_make_exec_result())
            MockExecutor.return_value = mock_exec

            loop = InnerLoop(project_dir=tmp_path, mode="test", task=task, workflow=wf)

            with patch("factory.models.InnerLoopConfig") as mock_config_cls:
                mock_config = MagicMock(spec=InnerLoopConfig)
                mock_config.aggregate = AggregateMethod(aggregate_method)
                mock_config_cls.return_value = mock_config
                record = loop.step()

        return record

    def test_step_aggregates_median(self, tmp_path: Path):
        record = self._make_loop_with_aggregate(
            tmp_path, "median", [0.2, 0.5, 0.9]
        )
        assert record.score_end == pytest.approx(0.5)

    def test_step_aggregates_median_even(self, tmp_path: Path):
        record = self._make_loop_with_aggregate(
            tmp_path, "median", [0.0, 0.4, 0.6, 1.0]
        )
        assert record.score_end == pytest.approx(0.5)

    def test_step_aggregates_max(self, tmp_path: Path):
        record = self._make_loop_with_aggregate(
            tmp_path, "max", [0.1, 0.3, 0.9]
        )
        assert record.score_end == pytest.approx(0.9)

    def test_step_aggregates_max_single(self, tmp_path: Path):
        record = self._make_loop_with_aggregate(
            tmp_path, "max", [0.42]
        )
        assert record.score_end == pytest.approx(0.42)

    def test_step_aggregates_all_pass_true(self, tmp_path: Path):
        record = self._make_loop_with_aggregate(
            tmp_path, "all_pass", [1.0, 1.0, 1.0]
        )
        assert record.score_end == pytest.approx(1.0)

    def test_step_aggregates_all_pass_false(self, tmp_path: Path):
        record = self._make_loop_with_aggregate(
            tmp_path, "all_pass", [1.0, 0.9, 1.0]
        )
        assert record.score_end == pytest.approx(0.0)

    def test_step_aggregates_all_pass_empty(self, tmp_path: Path):
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
