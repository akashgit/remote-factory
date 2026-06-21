"""Tier 4: Integration tests — run executor on workflows with mock agents."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.workflow.definitions import build_workflow, improve_workflow
from factory.workflow.executor import WorkflowExecutor
from factory.workflow.primitives import (
    DEFAULT_AGENT_POOL,
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    Verdict,
    VerdictType,
    Workflow,
)


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project with .factory/ directory structure."""
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    for sub in ("strategy", "reviews", "experiments", "archive"):
        (factory_dir / sub).mkdir()
    return tmp_path


# ── Mock workflow with stub agents ───────────────────────────────


class TestMockWorkflowExecution:
    async def test_full_trace(self, tmp_project: Path) -> None:
        """Run executor on a mock workflow. Verify full execution trace."""
        wf = Workflow(
            name="mock_pipeline",
            nodes={
                "study": FnNode(id="study", command="echo obs", writes={"obs.md"}),
                "researcher": FnNode(id="researcher", command="echo research", reads={"obs.md"}, writes={"research.md"}),
                "gate_research": GateNode(id="gate_research", evaluator_type="fn", evaluator_command="echo PROCEED", reads={"research.md"}),
                "strategist": FnNode(id="strategist", command="echo strategy", reads={"research.md"}, writes={"strategy.md"}),
                "gate_strategy": GateNode(id="gate_strategy", evaluator_type="fn", evaluator_command="echo PROCEED", reads={"strategy.md"}),
                "builder": FnNode(id="builder", command="echo build", reads={"strategy.md"}, writes={"build.md"}),
                "evaluator": FnNode(id="evaluator", command="echo eval", reads={"build.md"}, writes={"eval.md"}),
                "archivist": FnNode(id="archivist", command="echo archive", reads={"eval.md"}, writes={"archive.md"}, blocking=False),
            },
            edges=[
                Edge(source="study", target="researcher"),
                Edge(source="researcher", target="gate_research"),
                Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED),
                Edge(source="gate_research", target="researcher", condition=VerdictType.RELOOP),
                Edge(source="strategist", target="gate_strategy"),
                Edge(source="gate_strategy", target="builder", condition=VerdictType.PROCEED),
                Edge(source="gate_strategy", target="strategist", condition=VerdictType.RELOOP),
                Edge(source="builder", target="evaluator"),
                Edge(source="evaluator", target="archivist"),
            ],
            start_node="study",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        result = await executor.execute()

        assert result.success
        assert result.nodes_executed >= 6

        event_types = [e["type"] for e in result.events]
        assert "workflow.started" in event_types
        assert "workflow.completed" in event_types
        assert event_types.count("node.started") >= 6
        assert event_types.count("node.completed") >= 6
        assert "gate.verdict" in event_types

    async def test_node_order(self, tmp_project: Path) -> None:
        """Verify nodes execute in correct order."""
        wf = Workflow(
            name="order_test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "b": FnNode(id="b", command="echo b", reads={"a.txt"}, writes={"b.txt"}),
                "c": FnNode(id="c", command="echo c", reads={"b.txt"}, writes={"c.txt"}),
            },
            edges=[
                Edge(source="a", target="b"),
                Edge(source="b", target="c"),
            ],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        result = await executor.execute()

        node_starts = [
            e["node_id"] for e in result.events
            if e["type"] == "node.started"
        ]
        assert node_starts == ["a", "b", "c"]

    async def test_file_production(self, tmp_project: Path) -> None:
        """Verify files are tracked as produced."""
        wf = Workflow(
            name="files_test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"x.md", "y.md"}),
                "b": FnNode(id="b", command="echo b", reads={"x.md"}, writes={"z.md"}),
            },
            edges=[Edge(source="a", target="b")],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        result = await executor.execute()

        assert result.completed_files == {"x.md", "y.md", "z.md"}


# ── W₃ Improve with mock agents ─────────────────────────────────


class TestImproveWorkflowMock:
    async def test_improve_dry_run(self, tmp_project: Path) -> None:
        """Run W₃ in dry-run mode — verify structure executes correctly."""
        wf = improve_workflow()

        executor = WorkflowExecutor(
            wf, tmp_project, agent_pool=DEFAULT_AGENT_POOL, dry_run=True,
        )
        result = await executor.execute()

        assert result.success
        assert result.nodes_executed >= 5

        event_types = [e["type"] for e in result.events]
        assert "workflow.started" in event_types

    async def test_improve_archivist_nonblocking(self, tmp_project: Path) -> None:
        """Verify archivist in W₃ runs non-blocking."""
        wf = improve_workflow()
        archivist = wf.nodes.get("archivist")
        assert archivist is not None
        assert archivist.blocking is False


# ── W₁ Build with mock agents ───────────────────────────────────


class TestBuildWorkflowMock:
    async def test_build_dry_run(self, tmp_project: Path) -> None:
        """Run W₁ in dry-run mode — verify fork/join structure."""
        wf = build_workflow()

        executor = WorkflowExecutor(
            wf, tmp_project, agent_pool=DEFAULT_AGENT_POOL, dry_run=True,
        )
        result = await executor.execute()

        assert result.success
        assert result.nodes_executed >= 5

    async def test_build_three_researchers_parallel(self, tmp_project: Path) -> None:
        """Verify 3 researchers run from the fork node."""
        wf = build_workflow()
        fork = wf.nodes.get("fork_research")
        assert isinstance(fork, ForkNode)
        assert len(fork.targets) == 3
        assert "researcher_similar" in fork.targets
        assert "researcher_techstack" in fork.targets
        assert "researcher_pitfalls" in fork.targets


# ── Reloop with feedback appending ───────────────────────────────


class TestReloopFeedback:
    async def test_feedback_appended(self, tmp_project: Path) -> None:
        """Feedback from reloop is appended to target node context."""
        call_count = 0

        async def mock_evaluate_gate(node: GateNode) -> Verdict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return Verdict.reloop("a", "needs more detail", max_iterations=3)
            return Verdict.proceed()

        wf = Workflow(
            name="feedback_test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "gate": GateNode(id="gate", evaluator_type="fn", reads={"a.txt"}),
                "b": FnNode(id="b", command="echo b"),
            },
            edges=[
                Edge(source="a", target="gate"),
                Edge(source="gate", target="b", condition=VerdictType.PROCEED),
                Edge(source="gate", target="a", condition=VerdictType.RELOOP),
            ],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        executor._evaluate_gate = mock_evaluate_gate  # type: ignore[assignment]
        result = await executor.execute()

        assert result.success
        assert "needs more detail" in executor.node_context.get("a", "")


# ── Halt propagation ─────────────────────────────────────────────


class TestHaltPropagation:
    async def test_gate_halt_stops_workflow(self, tmp_project: Path) -> None:
        async def mock_evaluate_gate(node: GateNode) -> Verdict:
            return Verdict.halt("critical error detected")

        wf = Workflow(
            name="halt_test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "gate": GateNode(id="gate", evaluator_type="fn", reads={"a.txt"}),
                "b": FnNode(id="b", command="echo b"),
            },
            edges=[
                Edge(source="a", target="gate"),
                Edge(source="gate", target="b", condition=VerdictType.PROCEED),
            ],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True)
        executor._evaluate_gate = mock_evaluate_gate  # type: ignore[assignment]
        result = await executor.execute()

        assert result.halted
        assert "critical error" in result.halt_reason
        assert result.nodes_executed < 3

        event_types = [e["type"] for e in result.events]
        assert "workflow.halted" in event_types
