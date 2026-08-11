"""Tier 2: Executor tests — deterministic graph walker behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.workflow.executor import WorkflowExecutor
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ArtifactCheck,
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


# ── Auto-approve ────────────────────────────────────────────────


class TestAutoApprove:
    async def test_executor_auto_approve_logs(self, tmp_project: Path) -> None:
        """WorkflowExecutor(auto_approve=True) logs gate.auto_approved for user gates."""
        wf = Workflow(
            name="auto_approve_test",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "gate": GateNode(id="gate", evaluator_type="user", reads={"a.txt"}),
                "b": FnNode(id="b", command="echo b", writes={"b.txt"}),
            },
            edges=[
                Edge(source="a", target="gate"),
                Edge(source="gate", target="b", condition=VerdictType.PROCEED),
            ],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True, auto_approve=True)
        result = await executor.execute()

        assert result.success
        gate_events = [e for e in result.events if e["type"] == "gate.verdict"]
        assert len(gate_events) == 1
        assert gate_events[0]["verdict_type"] == VerdictType.PROCEED

    async def test_executor_default_still_proceeds(self, tmp_project: Path) -> None:
        """WorkflowExecutor(auto_approve=False) still proceeds through user gates."""
        wf = Workflow(
            name="default_user_gate",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "gate": GateNode(id="gate", evaluator_type="user", reads={"a.txt"}),
                "b": FnNode(id="b", command="echo b", writes={"b.txt"}),
            },
            edges=[
                Edge(source="a", target="gate"),
                Edge(source="gate", target="b", condition=VerdictType.PROCEED),
            ],
            start_node="a",
        )

        executor = WorkflowExecutor(wf, tmp_project, dry_run=True, auto_approve=False)
        result = await executor.execute()

        assert result.success
        gate_events = [e for e in result.events if e["type"] == "gate.verdict"]
        assert len(gate_events) == 1
        assert gate_events[0]["verdict_type"] == VerdictType.PROCEED

    async def test_auto_approve_emits_structured_log(self, tmp_project: Path) -> None:
        """auto_approve=True emits gate.auto_approved with gate_id and workflow name (non-dry-run)."""
        import structlog

        wf = Workflow(
            name="log_check_wf",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "gate": GateNode(id="gate", evaluator_type="user", reads={"a.txt"}),
                "b": FnNode(id="b", command="echo b", writes={"b.txt"}),
            },
            edges=[
                Edge(source="a", target="gate"),
                Edge(source="gate", target="b", condition=VerdictType.PROCEED),
            ],
            start_node="a",
        )

        captured: list[dict] = []

        def capture_log(_logger, _method, event_dict):
            captured.append(event_dict.copy())
            return event_dict

        structlog.configure(processors=[capture_log, structlog.dev.ConsoleRenderer()])

        try:
            executor = WorkflowExecutor(wf, tmp_project, dry_run=False, auto_approve=True)
            result = await executor.execute()
        finally:
            structlog.reset_defaults()

        assert result.success
        auto_approved = [e for e in captured if e.get("event") == "gate.auto_approved"]
        assert len(auto_approved) == 1
        assert auto_approved[0]["gate_id"] == "gate"
        assert auto_approved[0]["workflow"] == "log_check_wf"

    async def test_auto_approve_false_no_log(self, tmp_project: Path) -> None:
        """auto_approve=False does not emit gate.auto_approved log for user gates."""
        import structlog

        wf = Workflow(
            name="no_log_wf",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "gate": GateNode(id="gate", evaluator_type="user", reads={"a.txt"}),
                "b": FnNode(id="b", command="echo b", writes={"b.txt"}),
            },
            edges=[
                Edge(source="a", target="gate"),
                Edge(source="gate", target="b", condition=VerdictType.PROCEED),
            ],
            start_node="a",
        )

        captured: list[dict] = []

        def capture_log(_logger, _method, event_dict):
            captured.append(event_dict.copy())
            return event_dict

        structlog.configure(processors=[capture_log, structlog.dev.ConsoleRenderer()])

        try:
            executor = WorkflowExecutor(wf, tmp_project, dry_run=False, auto_approve=False)
            result = await executor.execute()
        finally:
            structlog.reset_defaults()

        assert result.success
        auto_approved = [e for e in captured if e.get("event") == "gate.auto_approved"]
        assert len(auto_approved) == 0


# ── Post-checks (headless enforcement) ──────────────────────────


class TestPostChecks:
    """Headless parity with skill-engine verification hooks.

    AgentNode.post_checks must be enforced by the executor, not only by
    the interactive SKILL.md path (verification.py hooks).
    """

    async def _run_agent_workflow(
        self,
        tmp_project: Path,
        post_checks: list[ArtifactCheck],
        monkeypatch: pytest.MonkeyPatch,
        artifact_content: str | None = None,
    ) -> WorkflowExecutor:
        from factory.agents import runner as agent_runner

        async def fake_invoke_agent(role, task, project_path, model=None, timeout=None):
            if artifact_content is not None:
                path = project_path / ".factory" / "strategy" / "research-local.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(artifact_content)
            return ("ok", 0)

        monkeypatch.setattr(agent_runner, "invoke_agent", fake_invoke_agent)

        wf = Workflow(
            name="post_checks",
            nodes={
                "researcher": AgentNode(
                    id="researcher",
                    role=AgentRole.RESEARCHER,
                    prompt_template="Write findings.",
                    post_checks=post_checks,
                ),
            },
            edges=[],
            start_node="researcher",
        )
        executor = WorkflowExecutor(wf, tmp_project, dry_run=False)
        await executor.execute()
        return executor

    async def test_missing_artifact_halts(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        executor = await self._run_agent_workflow(
            tmp_project,
            [ArtifactCheck(path=".factory/strategy/research-local.md", must_exist=True)],
            monkeypatch,
            artifact_content=None,
        )
        assert executor.result.halted
        assert "post-check failed" in executor.result.halt_reason
        assert "missing" in executor.result.halt_reason

    async def test_empty_artifact_halts(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        executor = await self._run_agent_workflow(
            tmp_project,
            [ArtifactCheck(path=".factory/strategy/research-local.md", must_exist=True)],
            monkeypatch,
            artifact_content="",
        )
        assert executor.result.halted
        assert "is empty" in executor.result.halt_reason

    async def test_min_size_halts(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        executor = await self._run_agent_workflow(
            tmp_project,
            [
                ArtifactCheck(
                    path=".factory/strategy/research-local.md",
                    must_exist=True,
                    min_size=50,
                )
            ],
            monkeypatch,
            artifact_content="short",
        )
        assert executor.result.halted
        assert "smaller than 50 bytes" in executor.result.halt_reason

    async def test_sentinel_any_match_passes(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        executor = await self._run_agent_workflow(
            tmp_project,
            [
                ArtifactCheck(
                    path=".factory/strategy/research-local.md",
                    must_exist=True,
                    must_contain=["SENTINEL_A", "SENTINEL_B"],
                )
            ],
            monkeypatch,
            artifact_content="has SENTINEL_B only",
        )
        assert executor.result.success
        assert not executor.result.halted

    async def test_sentinel_missing_halts(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        executor = await self._run_agent_workflow(
            tmp_project,
            [
                ArtifactCheck(
                    path=".factory/strategy/research-local.md",
                    must_exist=True,
                    must_contain=["SENTINEL_A", "SENTINEL_B"],
                )
            ],
            monkeypatch,
            artifact_content="no sentinels here",
        )
        assert executor.result.halted
        assert "missing required sentinel" in executor.result.halt_reason

    async def test_passing_checks_succeed(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        executor = await self._run_agent_workflow(
            tmp_project,
            [
                ArtifactCheck(
                    path=".factory/strategy/research-local.md",
                    must_exist=True,
                    min_size=4,
                    must_contain=["### Phase 1"],
                )
            ],
            monkeypatch,
            artifact_content="### Phase 1\n### Architecture\n",
        )
        assert executor.result.success
        assert not executor.result.halted

    async def test_no_post_checks_untouched(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        executor = await self._run_agent_workflow(
            tmp_project, [], monkeypatch, artifact_content=None
        )
        assert executor.result.success
        assert not executor.result.halted
