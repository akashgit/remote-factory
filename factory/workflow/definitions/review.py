"""W7: Review Mode workflow definition."""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)


def review_workflow() -> Workflow:
    """W7: Review Mode — verify eval dimensions, create factory.md, baseline eval.

    eval_test -> CEO gate (fix dims) -> mark_reviewed -> create_factory_md ->
    factory_init -> baseline_eval -> commit -> e2e_gate
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    nodes["eval_test"] = FnNode(
        id="eval_test",
        command="cd {project_path} && python eval/score.py",
        notes="Run the eval harness to test all discovered dimensions. Output is reviewed by the CEO gate to catch broken dimensions.",
        writes={".factory/reviews/eval-test-latest.md"},
    )

    nodes["gate_eval"] = GateNode(
        id="gate_eval",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Check eval output. Did all dimensions pass? "
            "If any dimension failed, dispatch the Builder to fix it "
            "(install missing tool, adjust command, remove broken dimension). "
            "PROCEED only when all dimensions produce valid scores."
        ),
        reads={".factory/reviews/eval-test-latest.md"},
    )

    nodes["mark_reviewed"] = FnNode(
        id="mark_reviewed",
        command=(
            'python3 -c "'
            "import json; from pathlib import Path; "
            "p = Path('{project_path}/.factory/eval_profile.json'); "
            "d = json.loads(p.read_text()); d['human_reviewed'] = True; "
            "p.write_text(json.dumps(d, indent=2))"
            '"'
        ),
        notes="Mark the eval profile as human-reviewed by setting the human_reviewed flag. Must run after the CEO approves all dimensions.",
        writes={".factory/eval_profile.json"},
    )

    nodes["create_factory_md"] = AgentNode(
        id="create_factory_md",
        role=AgentRole.CEO,
        prompt_template=(
            "Create factory.md from template. "
            "Copy the factory config template to the project root. "
            "Fill in: Goal, Scope, Guards, Eval command, Threshold, and Smoke Test. "
            "If .factory/eval_spec.json exists, populate the Eval Spec section. "
            "If .factory/strategy/current.md has a Research Configuration section, "
            "populate research sections (Research Target, Mutable/Fixed Surfaces, etc.)."
        ),
        reads={".factory/eval_profile.json"},
        writes={"factory.md"},
    )

    nodes["factory_init"] = FnNode(
        id="factory_init",
        command="factory init {project_path}",
        notes="Parse factory.md and generate .factory/config.json. Must run after factory.md is created.",
        reads={"factory.md"},
        writes={".factory/config.json"},
    )

    nodes["baseline_eval"] = FnNode(
        id="baseline_eval",
        command="factory eval {project_path}",
        notes="Run the first full eval after factory initialization to establish a baseline score.",
        reads={".factory/config.json"},
        writes={".factory/experiments/baseline.json"},
    )

    nodes["commit"] = FnNode(
        id="commit",
        command=(
            "cd {project_path} && git add factory.md eval/score.py .factory/ "
            '&& git commit -m "factory: initialize factory config and baseline eval"'
        ),
        notes="Commit the factory setup artifacts (factory.md, eval/score.py, .factory/) to git. Must run after baseline eval.",
        reads={"factory.md"},
    )

    nodes["gate_e2e"] = GateNode(
        id="gate_e2e",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "E2E verification gate. Verify the project runs end-to-end. "
            "Check the Smoke Test command in factory.md and run it. "
            "If this is a pre-existing project entering the factory for the first time, "
            "it MUST be verified before transitioning to Improve mode."
        ),
        reads={"factory.md", ".factory/config.json"},
    )

    edges = [
        Edge(source="eval_test", target="gate_eval"),
        Edge(source="gate_eval", target="mark_reviewed", condition=VerdictType.PROCEED),
        Edge(source="gate_eval", target="eval_test", condition=VerdictType.RELOOP),
        Edge(source="mark_reviewed", target="create_factory_md"),
        Edge(source="create_factory_md", target="factory_init"),
        Edge(source="factory_init", target="baseline_eval"),
        Edge(source="baseline_eval", target="commit"),
        Edge(source="commit", target="gate_e2e"),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state == ProjectState.EVALS_PENDING_REVIEW

    return Workflow(
        name="review",
        nodes=nodes,
        edges=edges,
        start_node="eval_test",
        trigger=trigger,
    )
