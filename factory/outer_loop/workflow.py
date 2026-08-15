"""Outer-loop workflow graph definition.

Defines outer_loop_workflow() returning a Workflow:
  study → seed_population → [generation loop: evaluate_batch → select → mutate →
  novelty_filter → designer_agent → gate_plateau] → holdout_audit → export_best → archivist
"""

from __future__ import annotations

from typing import Any

from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    Study,
    VerdictType,
    Workflow,
)


def outer_loop_workflow() -> Workflow:
    """Define the outer-loop evolutionary search workflow graph.

    study → seed_population → evaluate_batch → select → mutate →
    novelty_filter → designer_agent → gate_plateau →
    holdout_audit → export_best → archivist
    """
    nodes: dict[str, Any] = {}

    nodes["study"] = Study(
        id="study",
        command="factory study {project_path}",
        writes={".factory/strategy/observations.md"},
    )

    nodes["seed_population"] = FnNode(
        id="seed_population",
        command="factory outer-loop seed {project_path}",
        notes=(
            "Create the initial population from the seed workflow plus "
            "designer-generated variants (minimal + thorough). "
            "Reads SwarmConfig from .factory/outer-loop/config.json."
        ),
        reads={".factory/strategy/observations.md"},
        writes={".factory/outer-loop/state.json"},
    )

    nodes["evaluate_batch"] = FnNode(
        id="evaluate_batch",
        command="factory outer-loop evaluate {project_path}",
        notes=(
            "Parallel evaluation of population via SwarmEvaluator on training instances. "
            "Each candidate evaluated in isolated context."
        ),
        reads={".factory/outer-loop/state.json"},
        writes={".factory/outer-loop/fitness_cache.json"},
    )

    nodes["select"] = FnNode(
        id="select",
        command="factory outer-loop select {project_path}",
        notes="Tournament selection + MAP-Elites archive update.",
        reads={".factory/outer-loop/fitness_cache.json"},
        writes={".factory/outer-loop/map-elites/grid.json"},
    )

    nodes["mutate"] = FnNode(
        id="mutate",
        command="factory outer-loop mutate {project_path}",
        notes="Apply mutation operators via MutationStrategy to selected parents.",
        reads={".factory/outer-loop/map-elites/grid.json"},
        writes={".factory/outer-loop/state.json"},
    )

    nodes["novelty_filter"] = FnNode(
        id="novelty_filter",
        command="factory outer-loop filter {project_path}",
        notes="Reject near-duplicate candidates before evaluation.",
        reads={".factory/outer-loop/state.json"},
        writes={".factory/outer-loop/state.json"},
    )

    nodes["designer_agent"] = AgentNode(
        id="designer_agent",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Read the current best workflow and failure telemetry from "
            ".factory/outer-loop/. Propose targeted mutations based on "
            "execution data. Write mutation proposals to "
            ".factory/outer-loop/designer-proposals.json."
        ),
        reads={".factory/outer-loop/state.json", ".factory/outer-loop/fitness_cache.json"},
        writes={".factory/outer-loop/designer-proposals.json"},
    )

    nodes["gate_plateau"] = GateNode(
        id="gate_plateau",
        evaluator_type="fn",
        evaluator_command=(
            'python3 -c "'
            "import json; from pathlib import Path; "
            "state = json.loads(Path('{project_path}/.factory/outer-loop/state.json').read_text()); "
            "traj = state.get('score_trajectory', []); "
            "budget = state.get('budget_remaining', 0); "
            "plateau = len(traj) >= 4 and all(s <= traj[-4] for s in traj[-3:]); "
            "done = budget <= 0 or plateau; "
            "print('HALT' if done else 'PROCEED')"
            '"'
        ),
        reads={".factory/outer-loop/state.json"},
    )

    nodes["holdout_audit"] = FnNode(
        id="holdout_audit",
        command="factory outer-loop audit {project_path}",
        notes=(
            "Run best workflow on held-out instances via OverfitDetector. "
            "Flags if >15% score drop from training to holdout."
        ),
        reads={".factory/outer-loop/state.json"},
        writes={".factory/outer-loop/best/holdout_audit.json"},
    )

    nodes["export_best"] = FnNode(
        id="export_best",
        command="factory outer-loop export {project_path}",
        notes="Write best workflow as a portable .factory/workflows/<benchmark>-evolved.py.",
        reads={".factory/outer-loop/best/holdout_audit.json"},
        writes={".factory/outer-loop/best/workflow.py"},
    )

    nodes["archivist"] = AgentNode(
        id="archivist",
        role=AgentRole.ARCHIVIST,
        prompt_template=(
            "Archive the outer-loop evolutionary run results. "
            "Read the final state and best workflow from .factory/outer-loop/. "
            "Write a summary of the evolution to .factory/archive/outer-loop.md."
        ),
        reads={".factory/outer-loop/best/workflow.py", ".factory/outer-loop/state.json"},
        writes={".factory/archive/outer-loop.md"},
        blocking=False,
    )

    edges = [
        Edge(source="study", target="seed_population"),
        Edge(source="seed_population", target="evaluate_batch"),
        Edge(source="evaluate_batch", target="select"),
        Edge(source="select", target="mutate"),
        Edge(source="mutate", target="novelty_filter"),
        Edge(source="novelty_filter", target="designer_agent"),
        Edge(source="designer_agent", target="gate_plateau"),
        # Generation loop: continue or exit
        Edge(source="gate_plateau", target="evaluate_batch", condition=VerdictType.PROCEED),
        Edge(source="gate_plateau", target="holdout_audit", condition=VerdictType.HALT),
        Edge(source="holdout_audit", target="export_best"),
        Edge(source="export_best", target="archivist"),
    ]

    return Workflow(
        name="outer-loop",
        nodes=nodes,
        edges=edges,
        start_node="study",
    )
