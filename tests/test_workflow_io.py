"""Tests for WorkflowIO, SubWorkflowNode, and parallel IO safety validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from factory.workflow.events import HandoffEvent
from factory.workflow.executor import WorkflowExecutor
from factory.workflow.primitives import (
    Edge,
    FnNode,
    ForkNode,
    JoinNode,
    SubWorkflowNode,
    Workflow,
    WorkflowIO,
)


# ── WorkflowIO model ───────────────────────────────────────────────


class TestWorkflowIO:
    def test_default_empty(self) -> None:
        io = WorkflowIO()
        assert io.inputs == set()
        assert io.outputs == set()
        assert io.optional_inputs == set()
        assert io.optional_outputs == set()

    def test_with_values(self) -> None:
        io = WorkflowIO(
            inputs={".factory/strategy/observations.md"},
            outputs={".factory/strategy/research-combined.md"},
            optional_inputs={".factory/archive/"},
            optional_outputs={".factory/strategy/research-similar.md"},
        )
        assert ".factory/strategy/observations.md" in io.inputs
        assert ".factory/strategy/research-combined.md" in io.outputs
        assert ".factory/archive/" in io.optional_inputs
        assert ".factory/strategy/research-similar.md" in io.optional_outputs

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowIO(bad_field="nope")  # type: ignore[call-arg]

    def test_serialize_roundtrip(self) -> None:
        io = WorkflowIO(
            inputs={"a.txt", "b.txt"},
            outputs={"c.txt"},
        )
        data = io.model_dump()
        io2 = WorkflowIO.model_validate(data)
        assert io2.inputs == io.inputs
        assert io2.outputs == io.outputs


# ── SubWorkflowNode model ──────────────────────────────────────────


class TestSubWorkflowNode:
    def test_creation(self) -> None:
        node = SubWorkflowNode(
            id="sub_research",
            workflow_name="research",
        )
        assert node.workflow_name == "research"
        assert node.input_mapping == {}
        assert node.pass_context is True
        assert node.blocking is True

    def test_with_mapping(self) -> None:
        node = SubWorkflowNode(
            id="sub_build",
            workflow_name="build-verify",
            input_mapping={"plan.md": ".factory/strategy/current.md"},
            pass_context=False,
        )
        assert node.input_mapping == {"plan.md": ".factory/strategy/current.md"}
        assert node.pass_context is False

    def test_reads_writes(self) -> None:
        node = SubWorkflowNode(
            id="sub_qa",
            workflow_name="deep-qa",
            reads={".factory/reviews/builder-latest.md"},
            writes={".factory/reviews/qa-complete.md"},
        )
        assert ".factory/reviews/builder-latest.md" in node.reads
        assert ".factory/reviews/qa-complete.md" in node.writes

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            SubWorkflowNode(
                id="sub",
                workflow_name="test",
                bad_field="nope",  # type: ignore[call-arg]
            )

    def test_serialize_roundtrip(self) -> None:
        node = SubWorkflowNode(
            id="sub_test",
            workflow_name="test-wf",
            input_mapping={"a": "b"},
            pass_context=False,
        )
        data = node.model_dump()
        node2 = SubWorkflowNode.model_validate(data)
        assert node2.workflow_name == node.workflow_name
        assert node2.input_mapping == node.input_mapping
        assert node2.pass_context == node.pass_context


# ── Workflow with IO ────────────────────────────────────────────────


class TestWorkflowWithIO:
    def test_workflow_io_none_by_default(self) -> None:
        wf = Workflow(
            name="test",
            nodes={"a": FnNode(id="a", command="echo a")},
            edges=[],
            start_node="a",
        )
        assert wf.io is None

    def test_workflow_with_io(self) -> None:
        wf = Workflow(
            name="test",
            nodes={"a": FnNode(id="a", command="echo a", writes={"out.txt"})},
            edges=[],
            start_node="a",
            io=WorkflowIO(
                inputs={"in.txt"},
                outputs={"out.txt"},
            ),
        )
        assert wf.io is not None
        assert "in.txt" in wf.io.inputs
        assert "out.txt" in wf.io.outputs

    def test_workflow_with_sub_workflow_node_validates(self) -> None:
        wf = Workflow(
            name="parent",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
                "sub": SubWorkflowNode(
                    id="sub",
                    workflow_name="child",
                    reads={"a.txt"},
                ),
            },
            edges=[Edge(source="a", target="sub")],
            start_node="a",
        )
        # Graph validation succeeds structurally (registry checks are separate)
        issues = [
            i
            for i in wf.validate_graph()
            if "not found in registry" not in i and "no io contract" not in i
        ]
        assert issues == []


# ── HandoffEvent ────────────────────────────────────────────────────


class TestHandoffEvent:
    def test_creation(self) -> None:
        event = HandoffEvent(
            workflow_name="design",
            run_id="abc123",
            source_node="sub_research",
            target_workflow="research",
            files_handed=["obs.md"],
            files_missing=[],
            timestamp="2026-01-01T00:00:00Z",
        )
        assert event.workflow_name == "design"
        assert event.target_workflow == "research"
        assert event.files_handed == ["obs.md"]
        assert event.files_missing == []

    def test_defaults(self) -> None:
        event = HandoffEvent(
            workflow_name="test",
            run_id="123",
            source_node="node",
            target_workflow="child",
        )
        assert event.files_handed == []
        assert event.files_missing == []
        assert event.timestamp == ""


# ── Parallel IO safety validation ──────────────────────────────────


def _make_child_workflow(name: str, outputs: set[str]) -> Workflow:
    return Workflow(
        name=name,
        nodes={"a": FnNode(id="a", command="echo a", writes=outputs)},
        edges=[],
        start_node="a",
        io=WorkflowIO(outputs=outputs),
    )


class TestParallelIOValidation:
    def test_non_overlapping_outputs_pass(self) -> None:
        child_a = _make_child_workflow("wf_a", {"a.txt"})
        child_b = _make_child_workflow("wf_b", {"b.txt"})

        parent = Workflow(
            name="parent",
            nodes={
                "fork": ForkNode(id="fork", targets=["sub_a", "sub_b"]),
                "sub_a": SubWorkflowNode(id="sub_a", workflow_name="wf_a"),
                "sub_b": SubWorkflowNode(id="sub_b", workflow_name="wf_b"),
                "join": JoinNode(id="join", sources=["sub_a", "sub_b"]),
            },
            edges=[Edge(source="fork", target="join")],
            start_node="fork",
        )

        registry = {"wf_a": child_a, "wf_b": child_b, "parent": parent}
        with patch("factory.workflow.definitions.register_all", return_value=registry):
            issues = [i for i in parent.validate_graph() if "parallel conflict" in i]
            assert issues == []

    def test_overlapping_outputs_fail(self) -> None:
        child_a = _make_child_workflow("wf_a", {"shared.txt", "a.txt"})
        child_b = _make_child_workflow("wf_b", {"shared.txt", "b.txt"})

        parent = Workflow(
            name="parent",
            nodes={
                "fork": ForkNode(id="fork", targets=["sub_a", "sub_b"]),
                "sub_a": SubWorkflowNode(id="sub_a", workflow_name="wf_a"),
                "sub_b": SubWorkflowNode(id="sub_b", workflow_name="wf_b"),
                "join": JoinNode(id="join", sources=["sub_a", "sub_b"]),
            },
            edges=[Edge(source="fork", target="join")],
            start_node="fork",
        )

        registry = {"wf_a": child_a, "wf_b": child_b, "parent": parent}
        with patch("factory.workflow.definitions.register_all", return_value=registry):
            issues = parent.validate_graph()
            assert any("parallel conflict" in i and "shared.txt" in i for i in issues)

    def test_io_completeness_missing_io(self) -> None:
        child_no_io = Workflow(
            name="no_io_wf",
            nodes={"a": FnNode(id="a", command="echo a")},
            edges=[],
            start_node="a",
        )

        parent = Workflow(
            name="parent",
            nodes={
                "sub": SubWorkflowNode(id="sub", workflow_name="no_io_wf"),
            },
            edges=[],
            start_node="sub",
        )

        registry = {"no_io_wf": child_no_io, "parent": parent}
        with patch("factory.workflow.definitions.register_all", return_value=registry):
            issues = parent.validate_graph()
            assert any("no io contract" in i for i in issues)

    def test_circular_reference_detected(self) -> None:
        wf_a = Workflow(
            name="wf_a",
            nodes={
                "sub": SubWorkflowNode(id="sub", workflow_name="wf_b"),
            },
            edges=[],
            start_node="sub",
            io=WorkflowIO(),
        )
        wf_b = Workflow(
            name="wf_b",
            nodes={
                "sub": SubWorkflowNode(id="sub", workflow_name="wf_a"),
            },
            edges=[],
            start_node="sub",
            io=WorkflowIO(),
        )

        registry = {"wf_a": wf_a, "wf_b": wf_b}
        with patch("factory.workflow.definitions.register_all", return_value=registry):
            issues = wf_a.validate_graph()
            assert any("circular reference" in i for i in issues)

    def test_self_reference_detected(self) -> None:
        wf = Workflow(
            name="self_ref",
            nodes={
                "sub": SubWorkflowNode(id="sub", workflow_name="self_ref"),
            },
            edges=[],
            start_node="sub",
            io=WorkflowIO(),
        )

        registry = {"self_ref": wf}
        with patch("factory.workflow.definitions.register_all", return_value=registry):
            issues = wf.validate_graph()
            assert any("circular reference" in i for i in issues)

    def test_missing_workflow_in_registry(self) -> None:
        parent = Workflow(
            name="parent",
            nodes={
                "sub": SubWorkflowNode(id="sub", workflow_name="nonexistent"),
            },
            edges=[],
            start_node="sub",
        )

        registry = {"parent": parent}
        with patch("factory.workflow.definitions.register_all", return_value=registry):
            issues = parent.validate_graph()
            assert any("not found in registry" in i for i in issues)


# ── Executor: _execute_sub_workflow dry-run ────────────────────────


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()
    (factory_dir / "strategy").mkdir()
    (factory_dir / "reviews").mkdir()
    (factory_dir / "experiments").mkdir()
    (factory_dir / "archive").mkdir()
    return tmp_path


class TestExecuteSubWorkflowDryRun:
    async def test_sub_workflow_executes(self, tmp_project: Path) -> None:
        child_wf = Workflow(
            name="child",
            nodes={
                "step": FnNode(id="step", command="echo done", writes={"child_out.txt"}),
            },
            edges=[],
            start_node="step",
            io=WorkflowIO(outputs={"child_out.txt"}),
        )

        parent_wf = Workflow(
            name="parent",
            nodes={
                "pre": FnNode(id="pre", command="echo pre", writes={"pre.txt"}),
                "sub": SubWorkflowNode(id="sub", workflow_name="child", reads={"pre.txt"}),
            },
            edges=[Edge(source="pre", target="sub")],
            start_node="pre",
        )

        registry = {"child": child_wf, "parent": parent_wf}
        with patch("factory.workflow.definitions.register_all", return_value=registry):
            executor = WorkflowExecutor(parent_wf, tmp_project, dry_run=True)
            result = await executor.execute()

        assert result.success
        assert not result.halted
        assert "child_out.txt" in result.completed_files
        assert "pre.txt" in result.completed_files

    async def test_sub_workflow_missing_registry(self, tmp_project: Path) -> None:
        parent_wf = Workflow(
            name="parent",
            nodes={
                "sub": SubWorkflowNode(id="sub", workflow_name="missing"),
            },
            edges=[],
            start_node="sub",
        )

        with patch("factory.workflow.definitions.register_all", return_value={}):
            executor = WorkflowExecutor(parent_wf, tmp_project, dry_run=True)
            result = await executor.execute()

        assert result.halted
        assert "not found in registry" in result.halt_reason

    async def test_sub_workflow_missing_inputs_halts(self, tmp_project: Path) -> None:
        child_wf = Workflow(
            name="child",
            nodes={
                "step": FnNode(id="step", command="echo done"),
            },
            edges=[],
            start_node="step",
            io=WorkflowIO(inputs={"required.txt"}),
        )

        parent_wf = Workflow(
            name="parent",
            nodes={
                "sub": SubWorkflowNode(id="sub", workflow_name="child"),
            },
            edges=[],
            start_node="sub",
        )

        registry = {"child": child_wf}
        with patch("factory.workflow.definitions.register_all", return_value=registry):
            executor = WorkflowExecutor(parent_wf, tmp_project, dry_run=True)
            result = await executor.execute()

        assert result.halted
        assert "missing inputs" in result.halt_reason

    async def test_sub_workflow_inputs_satisfied_by_completed_files(
        self, tmp_project: Path
    ) -> None:
        child_wf = Workflow(
            name="child",
            nodes={
                "step": FnNode(
                    id="step",
                    command="echo done",
                    reads={"pre.txt"},
                    writes={"out.txt"},
                ),
            },
            edges=[],
            start_node="step",
            io=WorkflowIO(inputs={"pre.txt"}, outputs={"out.txt"}),
        )

        parent_wf = Workflow(
            name="parent",
            nodes={
                "pre": FnNode(id="pre", command="echo pre", writes={"pre.txt"}),
                "sub": SubWorkflowNode(id="sub", workflow_name="child", reads={"pre.txt"}),
            },
            edges=[Edge(source="pre", target="sub")],
            start_node="pre",
        )

        registry = {"child": child_wf}
        with patch("factory.workflow.definitions.register_all", return_value=registry):
            executor = WorkflowExecutor(parent_wf, tmp_project, dry_run=True)
            result = await executor.execute()

        assert result.success
        assert "out.txt" in result.completed_files

    async def test_sub_workflow_inputs_satisfied_by_disk(self, tmp_project: Path) -> None:
        (tmp_project / "on_disk.txt").write_text("exists")

        child_wf = Workflow(
            name="child",
            nodes={
                "step": FnNode(id="step", command="echo done", writes={"out.txt"}),
            },
            edges=[],
            start_node="step",
            io=WorkflowIO(inputs={"on_disk.txt"}, outputs={"out.txt"}),
        )

        parent_wf = Workflow(
            name="parent",
            nodes={
                "sub": SubWorkflowNode(id="sub", workflow_name="child"),
            },
            edges=[],
            start_node="sub",
        )

        registry = {"child": child_wf}
        with patch("factory.workflow.definitions.register_all", return_value=registry):
            executor = WorkflowExecutor(parent_wf, tmp_project, dry_run=True)
            result = await executor.execute()

        assert result.success

    async def test_fork_with_sub_workflow_targets(self, tmp_project: Path) -> None:
        child_a = Workflow(
            name="wf_a",
            nodes={
                "a": FnNode(id="a", command="echo a", writes={"a.txt"}),
            },
            edges=[],
            start_node="a",
            io=WorkflowIO(outputs={"a.txt"}),
        )
        child_b = Workflow(
            name="wf_b",
            nodes={
                "b": FnNode(id="b", command="echo b", writes={"b.txt"}),
            },
            edges=[],
            start_node="b",
            io=WorkflowIO(outputs={"b.txt"}),
        )

        parent_wf = Workflow(
            name="parent",
            nodes={
                "fork": ForkNode(id="fork", targets=["sub_a", "sub_b"]),
                "sub_a": SubWorkflowNode(id="sub_a", workflow_name="wf_a"),
                "sub_b": SubWorkflowNode(id="sub_b", workflow_name="wf_b"),
                "join": JoinNode(
                    id="join",
                    sources=["sub_a", "sub_b"],
                    reads={"a.txt", "b.txt"},
                ),
            },
            edges=[Edge(source="fork", target="join")],
            start_node="fork",
        )

        registry = {"wf_a": child_a, "wf_b": child_b}
        with patch("factory.workflow.definitions.register_all", return_value=registry):
            executor = WorkflowExecutor(parent_wf, tmp_project, dry_run=True)
            result = await executor.execute()

        assert result.success
        assert "a.txt" in result.completed_files
        assert "b.txt" in result.completed_files

    async def test_handoff_events_emitted(self, tmp_project: Path) -> None:
        child_wf = Workflow(
            name="child",
            nodes={
                "step": FnNode(id="step", command="echo done", writes={"out.txt"}),
            },
            edges=[],
            start_node="step",
            io=WorkflowIO(outputs={"out.txt"}),
        )

        parent_wf = Workflow(
            name="parent",
            nodes={
                "sub": SubWorkflowNode(id="sub", workflow_name="child"),
            },
            edges=[],
            start_node="sub",
        )

        registry = {"child": child_wf}
        with patch("factory.workflow.definitions.register_all", return_value=registry):
            executor = WorkflowExecutor(parent_wf, tmp_project, dry_run=True)
            result = await executor.execute()

        handoff_events = [e for e in result.events if e["type"].startswith("handoff.")]
        assert len(handoff_events) == 2
        assert handoff_events[0]["type"] == "handoff.started"
        assert handoff_events[1]["type"] == "handoff.completed"
        assert handoff_events[0]["target_workflow"] == "child"


# ── Existing workflows still validate ──────────────────────────────


class TestExistingWorkflowsStillValid:
    def test_all_registered_workflows_validate(self) -> None:
        from factory.workflow.definitions import register_all

        registry = register_all()
        for name, wf in registry.items():
            issues = [
                i
                for i in wf.validate_graph()
                if "not found in registry" not in i and "no io contract" not in i
            ]
            assert issues == [], f"workflow '{name}' has validation issues: {issues}"
