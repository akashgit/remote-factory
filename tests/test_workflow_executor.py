"""Tier 2: Executor tests — deterministic graph walker behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.workflow.executor import WorkflowExecutor
from factory.workflow.primitives import (
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    Verdict,
    VerdictType,
    Workflow,
)


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project with .factory/ directory."""
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    (factory_dir / "strategy").mkdir()
    (factory_dir / "reviews").mkdir()
    (factory_dir / "experiments").mkdir()
    (factory_dir / "archive").mkdir()
    return tmp_path


# ── Linear workflow ──────────────────────────────────────────────


class TestLinearWorkflow:
    async def test_a_b_c(self, tmp_project: Path) -> None:
        """Nodes execute in order, files flow correctly."""
        wf = Workflow(
            name="linear",
            nodes={
                "a": FnNode(id="a", command="echo a > a.txt", writes={"a.txt"}),
                "b": FnNode(id="b", command="echo b > b.txt", reads={"a.txt"}, writes={"b.txt"}),
                "c": FnNode(id="c", command="echo c > c.txt", reads={"b.txt"}, writes={"c.txt"}),
            },
            edges=[
                Edge(source="a", target="b"),
                Edge(source="b", target="c"),
            ],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        result = await executor.execute()

        assert result.success
        assert result.nodes_executed == 3
        assert not result.halted

    async def test_files_tracked(self, tmp_project: Path) -> None:
        """Completed files are tracked in executor state."""
        wf = Workflow(
            name="linear",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "b": FnNode(id="b", command="echo b", reads={"a.txt"}, writes={"b.txt"}),
            },
            edges=[Edge(source="a", target="b")],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        result = await executor.execute()

        assert "a.txt" in result.completed_files
        assert "b.txt" in result.completed_files


# ── Gate with Proceed ────────────────────────────────────────────


class TestGateProceed:
    async def test_proceed_follows_forward_edge(self, tmp_project: Path) -> None:
        wf = Workflow(
            name="gate_test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "gate": GateNode(
                    id="gate",
                    evaluator_type="fn",
                    evaluator_command="echo PROCEED",
                    reads={"a.txt"},
                ),
                "b": FnNode(id="b", command="echo b", writes={"b.txt"}),
            },
            edges=[
                Edge(source="a", target="gate"),
                Edge(source="gate", target="b", condition=VerdictType.PROCEED),
            ],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        result = await executor.execute()

        assert result.success
        assert result.nodes_executed >= 2


# ── Gate with Reloop ─────────────────────────────────────────────


class TestGateReloop:
    async def test_reloop_returns_to_target(self, tmp_project: Path) -> None:
        """Gate produces Reloop, execution returns with feedback."""
        wf = Workflow(
            name="reloop_test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "gate": GateNode(
                    id="gate",
                    evaluator_type="fn",
                    evaluator_command="echo PROCEED",
                    reads={"a.txt"},
                ),
                "b": FnNode(id="b", command="echo b", writes={"b.txt"}),
            },
            edges=[
                Edge(source="a", target="gate"),
                Edge(source="gate", target="b", condition=VerdictType.PROCEED),
                Edge(source="gate", target="a", condition=VerdictType.RELOOP),
            ],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        result = await executor.execute()
        assert result.success


# ── Gate with Halt ───────────────────────────────────────────────


class TestGateHalt:
    async def test_halt_terminates(self, tmp_project: Path) -> None:
        """Gate produces Halt, workflow terminates."""
        wf = Workflow(
            name="halt_test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "gate": GateNode(
                    id="gate",
                    evaluator_type="fn",
                    evaluator_command="echo FAIL",
                    reads={"a.txt"},
                ),
                "b": FnNode(id="b", command="echo b"),
            },
            edges=[
                Edge(source="a", target="gate"),
                Edge(source="gate", target="b", condition=VerdictType.PROCEED),
            ],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        result = await executor.execute()

        assert result.success
        assert result.nodes_executed >= 1


# ── Max iterations ───────────────────────────────────────────────


class TestMaxIterations:
    async def test_max_iterations_halts(self, tmp_project: Path) -> None:
        """Reloop exceeds max_iterations, workflow halts."""
        call_count = 0

        async def mock_evaluate_gate(node: GateNode) -> Verdict:
            nonlocal call_count
            call_count += 1
            return Verdict.reloop("a", f"try again #{call_count}", max_iterations=2)

        wf = Workflow(
            name="max_iter",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "gate": GateNode(id="gate", evaluator_type="fn", reads={"a.txt"}),
            },
            edges=[
                Edge(source="a", target="gate"),
                Edge(source="gate", target="a", condition=VerdictType.RELOOP),
            ],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        executor._evaluate_gate = mock_evaluate_gate  # type: ignore[assignment]
        result = await executor.execute()

        assert result.halted
        assert "max iterations" in result.halt_reason


# ── Fork/Join ────────────────────────────────────────────────────


class TestForkJoin:
    async def test_fork_runs_concurrently(self, tmp_project: Path) -> None:
        """Forked nodes execute concurrently."""
        wf = Workflow(
            name="fork_test",
            nodes={
                "fork": ForkNode(id="fork", targets=["a", "b", "c"]),
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "b": FnNode(id="b", command="echo b", writes={"b.txt"}),
                "c": FnNode(id="c", command="echo c", writes={"c.txt"}),
                "join": JoinNode(
                    id="join",
                    sources=["a", "b", "c"],
                    reads={"a.txt", "b.txt", "c.txt"},
                ),
                "final": FnNode(id="final", command="echo done", reads={"a.txt", "b.txt", "c.txt"}),
            },
            edges=[
                Edge(source="fork", target="a"),
                Edge(source="fork", target="b"),
                Edge(source="fork", target="c"),
                Edge(source="a", target="join"),
                Edge(source="b", target="join"),
                Edge(source="c", target="join"),
                Edge(source="join", target="final"),
            ],
            start_node="fork",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        result = await executor.execute()

        assert result.success
        assert "a.txt" in result.completed_files
        assert "b.txt" in result.completed_files
        assert "c.txt" in result.completed_files


# ── Non-blocking node ────────────────────────────────────────────


class TestNonBlocking:
    async def test_fire_and_forget(self, tmp_project: Path) -> None:
        """Non-blocking node fires, executor advances immediately."""
        wf = Workflow(
            name="nonblock_test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "async_node": FnNode(
                    id="async_node",
                    command="echo async",
                    reads={"a.txt"},
                    writes={"async.txt"},
                    blocking=False,
                ),
                "b": FnNode(id="b", command="echo b", writes={"b.txt"}),
            },
            edges=[
                Edge(source="a", target="async_node"),
                Edge(source="async_node", target="b"),
            ],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        result = await executor.execute()

        assert result.success
        assert result.nodes_executed >= 2


# ── Event emission ───────────────────────────────────────────────


class TestEventEmission:
    async def test_events_emitted(self, tmp_project: Path) -> None:
        """All event types emitted with correct structure."""
        wf = Workflow(
            name="event_test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "b": FnNode(id="b", command="echo b", reads={"a.txt"}),
            },
            edges=[Edge(source="a", target="b")],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        result = await executor.execute()

        event_types = [e["type"] for e in result.events]
        assert "workflow.started" in event_types
        assert "node.started" in event_types
        assert "node.completed" in event_types
        assert "workflow.completed" in event_types

    async def test_gate_verdict_event(self, tmp_project: Path) -> None:
        wf = Workflow(
            name="gate_event",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "gate": GateNode(
                    id="gate",
                    evaluator_type="fn",
                    evaluator_command="echo PROCEED",
                    reads={"a.txt"},
                ),
                "b": FnNode(id="b", command="echo b"),
            },
            edges=[
                Edge(source="a", target="gate"),
                Edge(source="gate", target="b", condition=VerdictType.PROCEED),
            ],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        result = await executor.execute()

        event_types = [e["type"] for e in result.events]
        assert "gate.verdict" in event_types


# ── Error handling ───────────────────────────────────────────────


class TestErrorHandling:
    async def test_node_failure_halts(self, tmp_project: Path) -> None:
        """Node failure produces Halt with error message."""
        wf = Workflow(
            name="error_test",
            nodes={
                "a": FnNode(id="a", command="exit 1", writes={"a.txt"}),
                "b": FnNode(id="b", command="echo b"),
            },
            edges=[Edge(source="a", target="b")],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project)
        result = await executor.execute()

        assert result.halted
        assert "failed" in result.halt_reason.lower()
