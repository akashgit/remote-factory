"""Tests for multi-mode compositor: sequential, parallel, and mixed execution."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.workflow.board import Board
from factory.workflow.compositor import (
    MultiModeExecutor,
    ParallelStep,
    SequentialStep,
    parse_mode_spec,
    validate_composition,
)
from factory.workflow.executor import ExecutionResult, WorkflowExecutor
from factory.workflow.primitives import Workflow


def _make_result(*, success: bool = True, halted: bool = False, halt_reason: str = "", nodes: int = 3) -> ExecutionResult:
    r = ExecutionResult()
    r.success = success
    r.halted = halted
    r.halt_reason = halt_reason
    r.nodes_executed = nodes
    r.duration_ms = 100.0
    return r


def _make_dummy_workflow(name: str) -> Workflow:
    from factory.workflow.primitives import FnNode

    node = FnNode(id="start", command="echo ok")
    return Workflow(
        name=name,
        nodes={"start": node},
        edges=[],
        start_node="start",
    )


@pytest.fixture
def tmp_board(tmp_path: Path) -> Board:
    board_path = tmp_path / ".factory" / "board.json"
    return Board(board_path, run_id="test-run", modes=["modeA", "modeB", "modeC"])


@pytest.fixture
def executor(tmp_path: Path, tmp_board: Board) -> MultiModeExecutor:
    return MultiModeExecutor(
        project_path=tmp_path,
        board=tmp_board,
        run_id="test-run",
        dry_run=True,
    )


# ── parse_mode_spec tests ──────────────────────────────────────


class TestParseModeSpec:
    def test_single_mode(self) -> None:
        steps = parse_mode_spec("discover")
        assert len(steps) == 1
        assert isinstance(steps[0], SequentialStep)
        assert steps[0].mode == "discover"

    def test_sequential_modes(self) -> None:
        steps = parse_mode_spec("discover,improve")
        assert len(steps) == 2
        assert all(isinstance(s, SequentialStep) for s in steps)

    def test_parallel_modes(self) -> None:
        steps = parse_mode_spec("a+b")
        assert len(steps) == 1
        assert isinstance(steps[0], ParallelStep)
        assert steps[0].modes == ["a", "b"]

    def test_mixed(self) -> None:
        steps = parse_mode_spec("discover,a+b,improve")
        assert len(steps) == 3
        assert isinstance(steps[0], SequentialStep)
        assert isinstance(steps[1], ParallelStep)
        assert isinstance(steps[2], SequentialStep)

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_mode_spec("")

    def test_trailing_comma_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_mode_spec("a,")


# ── validate_composition tests ─────────────────────────────────


class TestValidateComposition:
    def test_valid(self) -> None:
        steps = parse_mode_spec("discover,improve")
        errors = validate_composition(steps)
        assert errors == []

    def test_unknown_mode(self) -> None:
        steps = parse_mode_spec("nonexistent")
        errors = validate_composition(steps, registry_names={"discover"})
        assert any("unknown" in e for e in errors)

    def test_builtin_in_parallel_rejected(self) -> None:
        steps = parse_mode_spec("discover+improve")
        errors = validate_composition(steps)
        assert any("built-in" in e for e in errors)


# ── MultiModeExecutor tests ───────────────────────────────────


class TestSequentialExecution:
    def test_sequential_two_modes(self, executor: MultiModeExecutor, tmp_board: Board) -> None:
        call_order: list[str] = []

        async def mock_execute(self_exec: object) -> ExecutionResult:
            call_order.append(getattr(self_exec, "workflow").name)
            return _make_result()

        async def run() -> dict:
            with (
                patch("factory.workflow.compositor.WorkflowRegistry.get_workflow") as mock_get,
                patch("factory.workflow.compositor.WorkflowExecutor.execute", mock_execute),
            ):
                mock_get.side_effect = lambda name, path=None: _make_dummy_workflow(name)
                steps = [SequentialStep(mode="modeA"), SequentialStep(mode="modeB")]
                return await executor.execute(steps)

        results = asyncio.run(run())

        assert call_order == ["modeA", "modeB"]
        assert "modeA" in results
        assert "modeB" in results
        assert results["modeA"].success
        assert results["modeB"].success
        assert "modeA" in tmp_board.state.modes_completed
        assert "modeB" in tmp_board.state.modes_completed

    def test_halt_stops_chain(self, executor: MultiModeExecutor, tmp_board: Board) -> None:
        call_count = 0

        async def mock_execute(self_exec: object) -> ExecutionResult:
            nonlocal call_count
            call_count += 1
            return _make_result(success=False, halted=True, halt_reason="test halt")

        async def run() -> dict:
            with (
                patch("factory.workflow.compositor.WorkflowRegistry.get_workflow") as mock_get,
                patch("factory.workflow.compositor.WorkflowExecutor.execute", mock_execute),
            ):
                mock_get.side_effect = lambda name, path=None: _make_dummy_workflow(name)
                steps = [SequentialStep(mode="modeA"), SequentialStep(mode="modeB")]
                return await executor.execute(steps)

        results = asyncio.run(run())

        assert call_count == 1
        assert "modeA" in results
        assert "modeB" not in results
        assert results["modeA"].halted


class TestParallelExecution:
    def test_parallel_two_modes(self, executor: MultiModeExecutor, tmp_board: Board) -> None:
        executed: list[str] = []

        async def mock_execute(self_exec: object) -> ExecutionResult:
            name = getattr(self_exec, "workflow").name
            executed.append(name)
            await asyncio.sleep(0.01)
            return _make_result()

        async def run() -> dict:
            with (
                patch("factory.workflow.compositor.WorkflowRegistry.get_workflow") as mock_get,
                patch("factory.workflow.compositor.WorkflowExecutor.execute", mock_execute),
            ):
                mock_get.side_effect = lambda name, path=None: _make_dummy_workflow(name)
                steps = [ParallelStep(modes=["modeA", "modeB"])]
                return await executor.execute(steps)

        results = asyncio.run(run())

        assert set(executed) == {"modeA", "modeB"}
        assert results["modeA"].success
        assert results["modeB"].success
        assert "modeA" in tmp_board.state.modes_completed
        assert "modeB" in tmp_board.state.modes_completed

    def test_parallel_fail_fast(self, executor: MultiModeExecutor) -> None:
        async def mock_execute(self_exec: object) -> ExecutionResult:
            name = getattr(self_exec, "workflow").name
            if name == "modeA":
                raise RuntimeError("modeA exploded")
            await asyncio.sleep(5.0)
            return _make_result()

        async def run() -> dict:
            with (
                patch("factory.workflow.compositor.WorkflowRegistry.get_workflow") as mock_get,
                patch("factory.workflow.compositor.WorkflowExecutor.execute", mock_execute),
            ):
                mock_get.side_effect = lambda name, path=None: _make_dummy_workflow(name)
                steps = [ParallelStep(modes=["modeA", "modeB"])]
                return await executor.execute(steps)

        results = asyncio.run(run())

        assert results["modeA"].halted
        assert "exploded" in results["modeA"].halt_reason
        assert results["modeB"].halted


class TestMixedExecution:
    def test_mixed_sequential_parallel(self, executor: MultiModeExecutor, tmp_board: Board) -> None:
        call_order: list[str] = []

        async def mock_execute(self_exec: object) -> ExecutionResult:
            name = getattr(self_exec, "workflow").name
            call_order.append(name)
            await asyncio.sleep(0.01)
            return _make_result()

        async def run() -> dict:
            with (
                patch("factory.workflow.compositor.WorkflowRegistry.get_workflow") as mock_get,
                patch("factory.workflow.compositor.WorkflowExecutor.execute", mock_execute),
            ):
                mock_get.side_effect = lambda name, path=None: _make_dummy_workflow(name)
                steps = [
                    SequentialStep(mode="modeA"),
                    ParallelStep(modes=["modeB", "modeC"]),
                ]
                return await executor.execute(steps)

        results = asyncio.run(run())

        assert call_order[0] == "modeA"
        assert set(call_order[1:]) == {"modeB", "modeC"}
        assert all(r.success for r in results.values())


class TestBoardNamespaceIsolation:
    def test_board_namespace_isolation(self, executor: MultiModeExecutor, tmp_board: Board) -> None:
        async def mock_execute(self_exec: object) -> ExecutionResult:
            return _make_result()

        async def run() -> dict:
            with (
                patch("factory.workflow.compositor.WorkflowRegistry.get_workflow") as mock_get,
                patch("factory.workflow.compositor.WorkflowExecutor.execute", mock_execute),
            ):
                mock_get.side_effect = lambda name, path=None: _make_dummy_workflow(name)
                steps = [ParallelStep(modes=["modeA", "modeB"])]
                return await executor.execute(steps)

        asyncio.run(run())

        data_a = tmp_board.read("modeA", "result")
        data_b = tmp_board.read("modeB", "result")
        assert data_a is not None
        assert data_b is not None
        assert isinstance(data_a, dict)
        assert isinstance(data_b, dict)


class TestModePrefix:
    def test_mode_prefix_passed_to_executor(self, executor: MultiModeExecutor) -> None:
        captured_prefix: str | None = None

        original_init = WorkflowExecutor.__init__

        def capturing_init(self_exec, *a, **kw):
            nonlocal captured_prefix
            captured_prefix = kw.get("mode_prefix")
            original_init(self_exec, *a, **kw)

        async def mock_execute(self_exec: object) -> ExecutionResult:
            return _make_result()

        async def run() -> dict:
            with (
                patch("factory.workflow.compositor.WorkflowRegistry.get_workflow") as mock_get,
                patch("factory.workflow.compositor.WorkflowExecutor.__init__", capturing_init),
                patch("factory.workflow.compositor.WorkflowExecutor.execute", mock_execute),
            ):
                mock_get.side_effect = lambda name, path=None: _make_dummy_workflow(name)
                steps = [SequentialStep(mode="modeA")]
                return await executor.execute(steps)

        asyncio.run(run())

        assert captured_prefix == "modeA"

    def test_current_mode_set_under_lock(self, executor: MultiModeExecutor) -> None:
        async def mock_execute(self_exec: object) -> ExecutionResult:
            return _make_result()

        async def run() -> dict:
            with (
                patch("factory.workflow.compositor.WorkflowRegistry.get_workflow") as mock_get,
                patch("factory.workflow.compositor.WorkflowExecutor.execute", mock_execute),
            ):
                mock_get.side_effect = lambda name, path=None: _make_dummy_workflow(name)
                steps = [SequentialStep(mode="modeA")]
                return await executor.execute(steps)

        asyncio.run(run())

        assert executor._board.state.current_mode == "modeA"
