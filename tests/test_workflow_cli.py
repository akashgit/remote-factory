"""Tests for factory/workflow/cli.py — _cmd_run() coverage."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.workflow.cli import _cmd_run
from factory.workflow.executor import ExecutionResult
from factory.workflow.primitives import DEFAULT_AGENT_POOL


def _make_args(name: str, project_path: str, dry_run: bool = False) -> argparse.Namespace:
    return argparse.Namespace(name=name, project_path=project_path, dry_run=dry_run)


def _success_result() -> ExecutionResult:
    r = ExecutionResult()
    r.success = True
    r.halted = False
    r.nodes_executed = 3
    r.duration_ms = 42.0
    r.completed_files = {"a.txt", "b.txt"}
    return r


def _failure_result() -> ExecutionResult:
    r = ExecutionResult()
    r.success = False
    r.halted = True
    r.halt_reason = "gate rejected"
    r.nodes_executed = 2
    r.duration_ms = 10.0
    return r


class TestCmdRun:
    def test_unknown_workflow_returns_1(self, tmp_path: Path) -> None:
        args = _make_args("nonexistent", str(tmp_path))
        with patch("factory.workflow.cli.register_all", return_value={}):
            assert _cmd_run(args) == 1

    def test_success_returns_0(self, tmp_path: Path) -> None:
        mock_wf = MagicMock()
        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value=_success_result())

        with (
            patch("factory.workflow.cli.register_all", return_value={"build": mock_wf}),
            patch("factory.workflow.cli.WorkflowExecutor", return_value=mock_executor),
            patch("factory.agents.runner.begin_cycle_session", return_value="span-123") as mock_begin,
            patch("factory.agents.runner.complete_cycle_session") as mock_complete,
        ):
            result = _cmd_run(_make_args("build", str(tmp_path)))

        assert result == 0
        mock_begin.assert_called_once_with(tmp_path.resolve(), cycle_id="build")
        mock_complete.assert_called_once_with(tmp_path.resolve(), "span-123")

    def test_failure_returns_1(self, tmp_path: Path) -> None:
        mock_wf = MagicMock()
        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value=_failure_result())

        with (
            patch("factory.workflow.cli.register_all", return_value={"build": mock_wf}),
            patch("factory.workflow.cli.WorkflowExecutor", return_value=mock_executor),
            patch("factory.agents.runner.begin_cycle_session", return_value=None),
            patch("factory.agents.runner.complete_cycle_session"),
        ):
            result = _cmd_run(_make_args("build", str(tmp_path)))

        assert result == 1

    def test_complete_called_on_exception(self, tmp_path: Path) -> None:
        mock_wf = MagicMock()
        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(side_effect=RuntimeError("boom"))

        with (
            patch("factory.workflow.cli.register_all", return_value={"build": mock_wf}),
            patch("factory.workflow.cli.WorkflowExecutor", return_value=mock_executor),
            patch("factory.agents.runner.begin_cycle_session", return_value="span-456") as mock_begin,
            patch("factory.agents.runner.complete_cycle_session") as mock_complete,
        ):
            with pytest.raises(RuntimeError, match="boom"):
                _cmd_run(_make_args("build", str(tmp_path)))

        mock_begin.assert_called_once()
        mock_complete.assert_called_once_with(tmp_path.resolve(), "span-456")

    def test_executor_receives_correct_params(self, tmp_path: Path) -> None:
        mock_wf = MagicMock()
        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value=_success_result())

        with (
            patch("factory.workflow.cli.register_all", return_value={"improve": mock_wf}),
            patch("factory.workflow.cli.WorkflowExecutor", return_value=mock_executor) as mock_cls,
            patch("factory.agents.runner.begin_cycle_session", return_value=None),
            patch("factory.agents.runner.complete_cycle_session"),
        ):
            _cmd_run(_make_args("improve", str(tmp_path), dry_run=True))

        mock_cls.assert_called_once_with(
            mock_wf,
            tmp_path.resolve(),
            agent_pool=DEFAULT_AGENT_POOL,
            dry_run=True,
        )
