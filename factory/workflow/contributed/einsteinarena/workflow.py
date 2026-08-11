"""Einstein Arena benchmark workflow — mathematical optimization solver.

TODO: Implement the complete workflow in a separate session via create mode.

Expected design (placeholder):
- Node 1: builder (AgentNode) — generates solution.json from problem description
- Node 2: verifier (FnNode) — checks solution.json exists
- No RELOOP (single-shot optimization)

Reference implementations:
- factory/workflow/contributed/swebench/workflow.py — minimal bug-fix pipeline
- factory/workflow/contributed/programbench/workflow.py — adversarial verification loop

Task instruction location: /tmp/task-instruction.md (passed via --focus from Harbor)
Expected output: /workspace/solution.json (JSON matching problem-specific schema)
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    Workflow,
)

meta = {
    "name": "einsteinarena",
    "description": (
        "Einstein Arena benchmark mode — mathematical optimization problem solver. "
        "TODO: Full implementation pending."
    ),
}


def workflow() -> Workflow:
    """Build the Einstein Arena workflow (placeholder).

    TODO: Implement complete workflow with:
    1. Builder node with detailed prompt for mathematical optimization
    2. Verifier node to check solution.json existence and format
    3. Proper edge connections
    4. Trigger function that matches mode="einsteinarena"

    For now, returns a minimal stub that will fail gracefully.
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # TODO: Add builder node
    # nodes["builder"] = AgentNode(
    #     id="builder",
    #     role=AgentRole.BUILDER,
    #     model="opus",
    #     timeout=7200,
    #     prompt_template=(
    #         "TODO: Detailed prompt for Einstein Arena optimization problems\n"
    #         "1. Read /tmp/task-instruction.md\n"
    #         "2. Implement optimization algorithm\n"
    #         "3. Generate solution.json\n"
    #     ),
    # )

    # TODO: Add verifier node
    # nodes["verifier"] = FnNode(
    #     id="verifier",
    #     command="cd {project_path} && [ -f solution.json ] && echo 'pass' || echo 'fail'",
    # )

    # TODO: Add edges
    # edges = [Edge(source="builder", target="verifier")]

    # Placeholder: minimal stub node
    nodes["stub"] = FnNode(
        id="stub",
        command=(
            "echo 'ERROR: Einstein Arena workflow not yet implemented' >&2 && "
            "echo 'TODO: Implement factory/workflow/contributed/einsteinarena/workflow.py' >&2 && "
            "exit 1"
        ),
    )

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        """Trigger when mode=einsteinarena is specified."""
        return ctx.get("mode") == "einsteinarena"

    return Workflow(
        name="einsteinarena",
        nodes=nodes,
        edges=edges,
        start_node="stub",
        terminal=True,
        trigger=trigger,
    )
