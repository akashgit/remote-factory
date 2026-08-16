"""Outer loop workflow — evolutionary search for optimal workflow DAGs.

5-node pipeline: seed → evaluate → reflect → evolve → gate_converge
RELOOP from gate_converge back to evaluate until convergence criteria met.

The outer loop CEO orchestrates this workflow to evolve factory modes
against benchmarks. Each generation evaluates a population of candidate
workflows via InnerLoop.step(), reflects on exhaust, and produces informed
mutations for the next generation.
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)

meta = {
    "name": "outer-loop",
    "description": (
        "Outer loop evolutionary search — evolve workflow DAGs against benchmarks. "
        "seed → evaluate → reflect → evolve → gate_converge with RELOOP. "
        "Terminal mode — does not chain."
    ),
}


def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
    return ctx.get("mode") == "outer-loop"


def workflow() -> Workflow:
    """Build the outer loop workflow."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    nodes["seed"] = FnNode(
        id="seed",
        command="factory outer-loop calibrate {project_path}",
        writes={
            ".factory/outer_loop/modes/",
            ".factory/outer_loop/config.json",
        },
    )

    nodes["evaluate"] = FnNode(
        id="evaluate",
        command="factory outer-loop evaluate {project_path} --generation {generation}",
        reads={".factory/outer_loop/modes/"},
        writes={
            ".factory/outer_loop/results/",
            ".factory/outer_loop/eval_cache.json",
        },
    )

    nodes["reflect"] = FnNode(
        id="reflect",
        command="factory outer-loop reflect {project_path} --generation {generation}",
        reads={".factory/outer_loop/results/"},
        writes={".factory/outer_loop/reflections/"},
    )

    nodes["evolve"] = FnNode(
        id="evolve",
        command="factory outer-loop evolve {project_path} --generation {generation}",
        reads={
            ".factory/outer_loop/reflections/",
            ".factory/outer_loop/modes/",
        },
        writes={".factory/outer_loop/modes/"},
    )

    nodes["gate_converge"] = GateNode(
        id="gate_converge",
        evaluator_type="fn",
        evaluator_command="factory outer-loop status {project_path} --check-converge",
        reads={".factory/outer_loop/results/"},
    )

    edges = [
        Edge(source="seed", target="evaluate"),
        Edge(source="evaluate", target="reflect"),
        Edge(source="reflect", target="evolve"),
        Edge(source="evolve", target="gate_converge"),
        Edge(source="gate_converge", target="evaluate", condition=VerdictType.RELOOP),
    ]

    return Workflow(
        name="outer-loop",
        nodes=nodes,
        edges=edges,
        start_node="seed",
        terminal=True,
        trigger=trigger,
    )
