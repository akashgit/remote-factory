"""Behavior tests for graph-backed interactive workflow tools."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    Study,
    VerdictType,
    Workflow,
)
from factory.workflow.registry import WorkflowEntry, WorkflowRegistry
from factory.workflow.tool import (
    _detect_artifact,
    _find_reloop_target,
    _format_gate_task,
    _format_node_task,
    _format_progress,
    _get_workflow_cached,
    _phase_label,
    _resolve_original_project,
    _workflow_cache,
    tool_curr,
    tool_finalize,
    tool_init,
    tool_next,
    tool_overview,
    tool_status,
    tool_submit,
)


@pytest.fixture(autouse=True)
def reset_registry() -> None:
    WorkflowRegistry.reset()
    _workflow_cache.clear()


def register(workflow: Workflow) -> None:
    WorkflowRegistry._entries[workflow.name] = WorkflowEntry(
        name=workflow.name,
        description="test workflow",
        path="<test>",
        source="builtin",
        _workflow_fn=lambda: workflow,
    )


def simple_workflow() -> Workflow:
    return Workflow(
        name="tool-simple",
        start_node="study",
        nodes={
            "study": Study(
                id="study",
                command="factory study {project_path}",
                writes={".factory/strategy/observations.md"},
            ),
            "researcher": AgentNode(
                id="researcher",
                role=AgentRole.RESEARCHER,
                prompt_template="Research {project_path}",
                writes={".factory/reviews/researcher-latest.md"},
            ),
            "gate_research": GateNode(
                id="gate_research",
                evaluator_type="agent",
                gate_prompt="Review research",
            ),
            "builder": AgentNode(
                id="builder",
                role=AgentRole.BUILDER,
                prompt_template="Build",
                writes={".factory/reviews/builder-latest.md"},
            ),
        },
        edges=[
            Edge(source="study", target="researcher"),
            Edge(source="researcher", target="gate_research"),
            Edge(
                source="gate_research",
                target="builder",
                condition=VerdictType.PROCEED,
            ),
            Edge(
                source="gate_research",
                target="researcher",
                condition=VerdictType.RELOOP,
            ),
        ],
    )


def test_init_persists_thread_metadata_dsl_snapshot_and_checkpoint(tmp_path: Path) -> None:
    workflow = simple_workflow()
    register(workflow)

    session_dir = Path(tool_init(workflow.name, tmp_path))

    session = json.loads((session_dir / "session.json").read_text())
    assert session["workflow_name"] == workflow.name
    assert len(session["thread_id"]) == 12
    assert Workflow.model_validate_json(session["workflow_json"]).name == workflow.name
    assert (session_dir / "checkpoints.sqlite").exists()
    assert not (tmp_path / ".factory" / "tool_session" / "state.json").exists()
    assert "pointer_idx" not in session
    assert "completed" not in session


def test_init_rejects_unknown_workflow(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown workflow"):
        tool_init("missing", tmp_path)


def test_curr_and_next_project_pending_interrupt(tmp_path: Path) -> None:
    workflow = simple_workflow()
    register(workflow)
    tool_init(workflow.name, tmp_path)

    assert "Node: study" in tool_curr(tmp_path)
    assert "Type: Study" in tool_next(tmp_path)


def test_submit_resumes_graph_and_writes_agent_artifact(tmp_path: Path) -> None:
    workflow = simple_workflow()
    register(workflow)
    tool_init(workflow.name, tmp_path)

    assert tool_submit(tmp_path, "study", "observations") == "CONTINUE"
    assert "Node: researcher" in tool_curr(tmp_path)
    assert tool_submit(tmp_path, "researcher", "research findings") == "CONTINUE"
    assert (
        tmp_path / ".factory" / "reviews" / "researcher-latest.md"
    ).read_text() == "research findings"
    assert tool_next(tmp_path).startswith("GATE\n")


def test_submit_rejects_non_pending_node(tmp_path: Path) -> None:
    workflow = simple_workflow()
    register(workflow)
    tool_init(workflow.name, tmp_path)

    with pytest.raises(ValueError, match="not pending"):
        tool_submit(tmp_path, "builder", "early")


def test_gate_reloop_is_persisted_and_returns_to_target(tmp_path: Path) -> None:
    workflow = Workflow(
        name="tool-reloop",
        start_node="builder",
        nodes={
            "builder": AgentNode(
                id="builder",
                role=AgentRole.BUILDER,
                writes={".factory/reviews/builder-latest.md"},
            ),
            "gate": GateNode(id="gate", evaluator_type="agent", gate_prompt="Check"),
            "done": FnNode(id="done", command="echo done"),
        },
        edges=[
            Edge(source="builder", target="gate"),
            Edge(source="gate", target="done", condition=VerdictType.PROCEED),
            Edge(source="gate", target="builder", condition=VerdictType.RELOOP),
        ],
    )
    register(workflow)
    tool_init(workflow.name, tmp_path)
    tool_submit(tmp_path, "builder", "first")

    result = tool_submit(
        tmp_path,
        "gate",
        'RETRY target=builder feedback="tests still fail"',
    )

    assert result.startswith("RETRY")
    current = tool_curr(tmp_path)
    assert "Node: builder" in current
    assert "Iteration: 1/3" in current
    assert "tests still fail" in current


def test_gate_halt_completes_thread_without_running_successor(tmp_path: Path) -> None:
    workflow = Workflow(
        name="tool-halt",
        start_node="gate",
        nodes={
            "gate": GateNode(id="gate", evaluator_type="user"),
            "never": FnNode(id="never", command="echo never"),
        },
        edges=[
            Edge(source="gate", target="never", condition=VerdictType.PROCEED),
        ],
    )
    register(workflow)
    tool_init(workflow.name, tmp_path)

    assert tool_curr(tmp_path).startswith("APPROVAL_NEEDED")
    result = tool_submit(tmp_path, "gate", 'HALT reason="not approved"')
    assert result == "HALT\nnot approved"
    assert "Status:   halted" in tool_status(tmp_path)
    assert "✓ gate" in tool_status(tmp_path)
    assert "○ never" in tool_status(tmp_path)


def test_fn_gate_runs_inside_graph_after_submission(tmp_path: Path) -> None:
    workflow = Workflow(
        name="tool-fn-gate",
        start_node="builder",
        nodes={
            "builder": AgentNode(id="builder", role=AgentRole.BUILDER),
            "gate": GateNode(
                id="gate",
                evaluator_type="fn",
                evaluator_command="echo PROCEED",
            ),
            "archive": AgentNode(id="archive", role=AgentRole.ARCHIVIST),
        },
        edges=[
            Edge(source="builder", target="gate"),
            Edge(source="gate", target="archive", condition=VerdictType.PROCEED),
        ],
    )
    register(workflow)
    tool_init(workflow.name, tmp_path)

    assert tool_submit(tmp_path, "builder", "built") == "CONTINUE"
    assert "Node: archive" in tool_curr(tmp_path)
    assert "gate" in tool_status(tmp_path)


def test_next_auto_resumes_fresh_artifact(tmp_path: Path) -> None:
    workflow = simple_workflow()
    register(workflow)
    tool_init(workflow.name, tmp_path)
    observations = tmp_path / ".factory" / "strategy" / "observations.md"
    observations.parent.mkdir(parents=True)
    observations.write_text("fresh observations " * 5)

    result = tool_next(tmp_path)

    assert "Node: researcher" in result
    assert "✓ study" in tool_overview(tmp_path)


def test_next_ignores_stale_artifact(tmp_path: Path) -> None:
    workflow = simple_workflow()
    register(workflow)
    observations = tmp_path / ".factory" / "strategy" / "observations.md"
    observations.parent.mkdir(parents=True)
    observations.write_text("stale observations " * 5)
    stale = time.time() - 60
    observations.touch()
    observations.chmod(0o644)
    import os

    os.utime(observations, (stale, stale))
    tool_init(workflow.name, tmp_path)

    assert "Node: study" in tool_next(tmp_path)


def test_next_dry_run_never_auto_resumes(tmp_path: Path) -> None:
    workflow = simple_workflow()
    register(workflow)
    tool_init(workflow.name, tmp_path)
    observations = tmp_path / ".factory" / "strategy" / "observations.md"
    observations.parent.mkdir(parents=True)
    observations.write_text("fresh observations " * 5)

    assert "Node: study" in tool_next(tmp_path, dry_run=True)
    assert "Node: study" in tool_curr(tmp_path)


def test_finalize_does_not_fake_completion(tmp_path: Path) -> None:
    workflow = simple_workflow()
    register(workflow)
    tool_init(workflow.name, tmp_path)

    result = tool_finalize(tmp_path)

    assert result.startswith("Pending graph tasks: study")
    assert "Progress: 0/4" in tool_status(tmp_path)


def test_parallel_interrupts_share_one_graph_thread(tmp_path: Path) -> None:
    workflow = Workflow(
        name="tool-parallel",
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
    register(workflow)
    tool_init(workflow.name, tmp_path)

    current = tool_curr(tmp_path)
    assert current.startswith("PARALLEL")
    assert "Node: a" in current
    assert "Node: b" in current
    assert tool_submit(tmp_path, "a", "A") == "CONTINUE"
    assert tool_submit(tmp_path, "b", "B") == "CONTINUE"
    assert "Node: done" in tool_curr(tmp_path)
    assert tool_submit(tmp_path, "done", "done") == "DONE"
    assert "Progress: 5/5" in tool_status(tmp_path)


def test_status_overview_and_curr_are_read_only(tmp_path: Path) -> None:
    workflow = simple_workflow()
    register(workflow)
    tool_init(workflow.name, tmp_path)

    status = tool_status(tmp_path)
    overview = tool_overview(tmp_path, fmt="phased")
    current = tool_curr(tmp_path)

    assert "Thread:" in status
    assert "Progress: 0/4" in status
    assert "Phase 1" in overview
    assert "CURRENT" in overview
    assert "Node: study" in current
    assert "Progress: 0/4" in tool_status(tmp_path)


def test_status_without_session(tmp_path: Path) -> None:
    assert tool_status(tmp_path).startswith("No active session")


def test_workflow_cache_is_process_local_only(tmp_path: Path) -> None:
    workflow = simple_workflow()
    register(workflow)

    assert _get_workflow_cached(workflow.name, tmp_path) is workflow
    assert not (tmp_path / ".factory" / "langgraph" / "workflow_cache.json").exists()


def test_resolve_original_project_variants() -> None:
    assert _resolve_original_project(
        Path("/repo/.factory-worktrees/run-1")
    ) == Path("/repo")
    assert _resolve_original_project(
        Path("/repo/.factory/worktrees/run-1")
    ) == Path("/repo")
    assert _resolve_original_project(Path("/repo")) == Path("/repo")


def test_helper_formatting_and_reloop_lookup(tmp_path: Path) -> None:
    workflow = simple_workflow()
    _workflow_cache[f"{tmp_path}:{workflow.name}"] = workflow
    gate = workflow.nodes["gate_research"]
    researcher = workflow.nodes["researcher"]
    state = {
        "workflow_name": workflow.name,
        "topo_order": ["study", "researcher", "gate_research", "builder"],
        "completed": {"study": "done"},
        "iteration_counts": {},
        "feedback_log": {},
    }

    assert _phase_label("researcher", researcher) == "Researcher"
    assert "Node: researcher" in _format_node_task(
        "researcher", researcher, workflow, state, tmp_path
    )
    assert isinstance(gate, GateNode)
    assert "RETRY" in _format_gate_task("gate_research", gate, state, tmp_path)
    assert "✓ study" in _format_progress(
        state, workflow, tmp_path, {"researcher"}
    )
    assert _find_reloop_target(workflow, "gate_research") == "researcher"


def test_detect_artifact_uses_declared_agent_write(tmp_path: Path) -> None:
    node = AgentNode(
        id="custom",
        role=AgentRole.BUILDER,
        writes={"custom/output.md"},
    )
    output = tmp_path / "custom" / "output.md"
    output.parent.mkdir()
    output.write_text("result")

    assert _detect_artifact("custom", node, tmp_path) == "result"
