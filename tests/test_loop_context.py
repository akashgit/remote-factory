"""Loop-context rendering from persisted LangGraph state."""

from __future__ import annotations

from pathlib import Path

from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)
from factory.workflow.tool import _find_loop_context, _format_node_task


def reloop_workflow() -> Workflow:
    return Workflow(
        name="loop-context",
        start_node="builder",
        nodes={
            "builder": AgentNode(
                id="builder",
                role=AgentRole.BUILDER,
                reads={"strategy.md"},
                writes={"build.md"},
            ),
            "gate": GateNode(
                id="gate",
                evaluator_type="agent",
                gate_prompt="Run QA checks",
            ),
            "archive": FnNode(id="archive", command="echo archive"),
        },
        edges=[
            Edge(source="builder", target="gate"),
            Edge(source="gate", target="archive", condition=VerdictType.PROCEED),
            Edge(source="gate", target="builder", condition=VerdictType.RELOOP),
        ],
    )


def test_non_target_has_no_loop_context(tmp_path: Path) -> None:
    state = {"topo_order": ["builder", "gate", "archive"]}
    assert _find_loop_context("archive", reloop_workflow(), state, tmp_path) == ""


def test_first_attempt_shows_topology_without_feedback(tmp_path: Path) -> None:
    state = {
        "topo_order": ["builder", "gate", "archive"],
        "iteration_counts": {},
        "feedback_log": {},
    }

    result = _find_loop_context("builder", reloop_workflow(), state, tmp_path)

    assert "## LOOP CONTEXT" in result
    assert "Iteration: 0/3" in result
    assert "Run QA checks" in result
    assert "Loop topology" in result
    assert "Feedback history" not in result


def test_persisted_iteration_and_feedback_are_rendered(tmp_path: Path) -> None:
    state = {
        "topo_order": ["builder", "gate", "archive"],
        "iteration_counts": {"gate->builder": 2},
        "feedback_log": {
            "builder": [
                {
                    "gate": "gate",
                    "iteration": 1,
                    "feedback": "first issue",
                    "timestamp": 1.0,
                },
                {
                    "gate": "gate",
                    "iteration": 2,
                    "feedback": "second issue",
                    "timestamp": 2.0,
                },
            ]
        },
    }

    result = _find_loop_context("builder", reloop_workflow(), state, tmp_path)

    assert "Iteration: 2/3" in result
    assert "Feedback history" in result
    assert "first issue" in result
    assert "second issue" in result


def test_final_attempt_warning(tmp_path: Path) -> None:
    state = {
        "topo_order": ["builder", "gate", "archive"],
        "iteration_counts": {"gate->builder": 3},
        "feedback_log": {},
    }
    result = _find_loop_context("builder", reloop_workflow(), state, tmp_path)
    assert "FINAL ATTEMPT" in result


def test_node_task_includes_loop_context(tmp_path: Path) -> None:
    workflow = reloop_workflow()
    state = {
        "topo_order": ["builder", "gate", "archive"],
        "iteration_counts": {"gate->builder": 1},
        "feedback_log": {
            "builder": [{
                "gate": "gate",
                "iteration": 1,
                "feedback": "fix tests",
                "timestamp": 1.0,
            }]
        },
    }
    result = _format_node_task(
        "builder",
        workflow.nodes["builder"],
        workflow,
        state,
        tmp_path,
    )
    assert "## LOOP CONTEXT" in result
    assert "fix tests" in result
