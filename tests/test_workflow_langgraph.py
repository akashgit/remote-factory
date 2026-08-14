"""Canonical LangGraph compilation, persistence, and durability tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.workflow.definitions import register_all
from factory.workflow.executor import WorkflowExecutor
from factory.workflow.langgraph import compile_langgraph, initial_state
from factory.workflow.primitives import (
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    VerdictType,
    Workflow,
)


def test_every_builtin_workflow_compiles_directly_to_langgraph(tmp_path: Path) -> None:
    for workflow in register_all().values():
        runtime = WorkflowExecutor(workflow, tmp_path, dry_run=True)
        graph = compile_langgraph(workflow, runtime)
        assert graph.name == workflow.name


async def test_thread_manifest_reconstructs_exact_dry_run_for_resume(
    tmp_path: Path,
) -> None:
    workflow = Workflow(
        name="manifest-resume",
        start_node="before",
        nodes={
            "before": FnNode(id="before", command="exit 99"),
            "approval": GateNode(id="approval", evaluator_type="user"),
            "after": FnNode(id="after", command="exit 99"),
        },
        edges=[
            Edge(source="before", target="approval"),
            Edge(source="approval", target="after", condition=VerdictType.PROCEED),
        ],
    )
    executor = WorkflowExecutor(workflow, tmp_path, dry_run=True, thread_id="resume-me")

    paused = await executor.execute()

    assert paused.interrupted
    manifest = json.loads(
        (tmp_path / ".factory" / "langgraph" / "threads" / "resume-me.json").read_text()
    )
    assert manifest["dry_run"] is True
    restarted = WorkflowExecutor.from_thread(tmp_path, "resume-me")
    assert restarted.workflow.model_dump_json() == workflow.model_dump_json()
    completed = await restarted.resume("PROCEED")
    assert completed.success
    assert completed.completed_nodes == {"before", "approval", "after"}


async def test_interactive_parallel_thread_resumes_after_process_restart(
    tmp_path: Path,
) -> None:
    workflow = Workflow(
        name="parallel-resume",
        start_node="fork",
        nodes={
            "fork": ForkNode(id="fork", targets=["a", "b"]),
            "a": FnNode(id="a", command="echo a"),
            "b": FnNode(id="b", command="echo b"),
            "join": JoinNode(id="join", sources=["a", "b"]),
            "done": FnNode(id="done", command="echo done"),
        },
        edges=[
            Edge(source="fork", target="a"),
            Edge(source="fork", target="b"),
            Edge(source="a", target="join"),
            Edge(source="b", target="join"),
            Edge(source="join", target="done"),
        ],
    )
    first = WorkflowExecutor(
        workflow,
        tmp_path,
        interactive=True,
        thread_id="parallel-thread",
    )
    paused = await first.execute()
    assert {item["value"]["node_id"] for item in paused.interrupts} == {"a", "b"}

    restarted = WorkflowExecutor.from_thread(tmp_path, "parallel-thread")
    branch_values = {str(item["id"]): item["value"]["node_id"] for item in paused.interrupts}
    paused_at_done = await restarted.resume(branch_values)
    assert paused_at_done.interrupts[0]["value"]["node_id"] == "done"

    restarted_again = WorkflowExecutor.from_thread(tmp_path, "parallel-thread")
    completed = await restarted_again.resume("finished")
    assert completed.success
    assert completed.completed_nodes == {"fork", "a", "b", "join", "done"}


async def test_completion_receipt_prevents_duplicate_side_effect(tmp_path: Path) -> None:
    node = FnNode(id="effect", command="printf x >> effect.txt")
    workflow = Workflow(name="receipt", nodes={"effect": node}, edges=[], start_node="effect")
    executor = WorkflowExecutor(workflow, tmp_path, thread_id="receipt-thread")
    state = initial_state(workflow, str(tmp_path), executor.run_id)

    await executor.run_node(node, state)
    await executor.run_node(node, state)

    assert (tmp_path / "effect.txt").read_text() == "x"
    receipt = json.loads(
        (
            tmp_path
            / ".factory"
            / "langgraph"
            / "receipts"
            / "receipt-thread"
            / "effect-1.json"
        ).read_text()
    )
    assert receipt["status"] == "completed"
    assert receipt["operation_id"] == "receipt-thread:effect:1"


async def test_started_receipt_blocks_ambiguous_side_effect_retry(tmp_path: Path) -> None:
    node = FnNode(id="effect", command="printf x >> effect.txt")
    workflow = Workflow(name="ambiguous", nodes={"effect": node}, edges=[], start_node="effect")
    executor = WorkflowExecutor(workflow, tmp_path, thread_id="ambiguous-thread")
    receipt = (
        tmp_path
        / ".factory"
        / "langgraph"
        / "receipts"
        / "ambiguous-thread"
        / "effect-1.json"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({
        "operation_id": "ambiguous-thread:effect:1",
        "node_id": "effect",
        "attempt": 1,
        "status": "started",
    }))

    with pytest.raises(RuntimeError, match="ambiguous prior attempt"):
        await executor.run_node(
            node,
            initial_state(workflow, str(tmp_path), executor.run_id),
        )
    assert not (tmp_path / "effect.txt").exists()


async def test_halt_cleanup_edge_runs_but_thread_remains_halted(tmp_path: Path) -> None:
    workflow = Workflow(
        name="halt-cleanup",
        start_node="approval",
        nodes={
            "approval": GateNode(id="approval", evaluator_type="user"),
            "cleanup": FnNode(id="cleanup", command="echo cleanup"),
        },
        edges=[
            Edge(source="approval", target="cleanup", condition=VerdictType.HALT),
        ],
    )
    executor = WorkflowExecutor(workflow, tmp_path, dry_run=True)
    paused = await executor.execute()
    assert paused.interrupted

    result = await executor.resume('HALT reason="rejected"')

    assert result.completed
    assert result.halted
    assert not result.success
    assert result.halt_reason == "rejected"
    assert "cleanup" in result.completed_nodes
