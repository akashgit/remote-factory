"""Lumen RL Workflow for Einstein Arena - MVP version."""

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

meta = {
    "name": "lumen",
    "description": "Lumen RL training workflow for Einstein Arena (MVP)",
}


def workflow() -> Workflow:
    """Build the Lumen workflow graph."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 1: Study ──────────────────────────────────────────
    nodes["study"] = FnNode(
        id="study",
        command=(
            "mkdir -p {project_path}/.factory/lumen && "
            "python3 benchmarks/einsteinarena/tools/add_sota_to_instruction.py {task_name} || true && "
            'echo \'{"iteration": 0, "best_score": null}\' > {project_path}/.factory/lumen/state.json && '
            "echo 'Study complete'"
        ),
        writes={".factory/lumen/state.json"},
    )

    # ── Node 2: Lumen Context Agent ────────────────────────────
    nodes["lumen_context_agent"] = AgentNode(
        id="lumen_context_agent",
        role=AgentRole.LUMEN_CONTEXT_AGENT,
        model="opus",
        timeout=1800,
        prompt_template=(
            "You are the Lumen Context Agent. Generate 8 optimization prompts.\n\n"
            "Read: benchmarks/einsteinarena/{task_name}/instruction.md\n"
            "Read: .factory/lumen/state.json\n\n"
            "If iteration > 0, read: .factory/lumen/iteration_{{prev_iteration}}/evaluation_results.json\n\n"
            "Output: .factory/lumen/iteration_{{current_iteration}}/prompts.json\n\n"
            "Follow the format specified in factory/agents/prompts/lumen_context_agent.md"
        ),
        reads={".factory/lumen/state.json"},
        writes={".factory/lumen/iteration_{current_iteration}/prompts.json"},
    )

    # ── Node 3: RL Training ─────────────────────────────────────
    nodes["rl_train"] = FnNode(
        id="rl_train",
        command=(
            "cd {project_path} && "
            'ITER=$(python3 -c "import json; print(json.load(open(\'.factory/lumen/state.json\'))[\'iteration\'])") && '
            "python3 -m factory.lumen.train "
            "--task {task_name} "
            "--task-dir benchmarks/einsteinarena/{task_name} "
            "--project-path {project_path} "
            "--iteration $ITER "
            "--num-rollouts-per-prompt {rollouts_per_prompt} "
            "--model-path {model_path} "
            "{mock_flag}"
        ),
        reads={".factory/lumen/state.json", ".factory/lumen/iteration_{current_iteration}/prompts.json"},
        writes={
            ".factory/lumen/iteration_{current_iteration}/rollouts.jsonl",
            ".factory/lumen/iteration_{current_iteration}/evaluation_results.json",
        },
    )

    # ── Node 4: Check Gate ──────────────────────────────────────
    nodes["check_gate"] = GateNode(
        id="check_gate",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            "python3 -c \""
            "import json, re;"
            "results = json.load(open('.factory/lumen/iteration_' + str(json.load(open('.factory/lumen/state.json'))['iteration']) + '/evaluation_results.json'));"
            "state = json.load(open('.factory/lumen/state.json'));"
            "md = open('benchmarks/einsteinarena/{task_name}/instruction.md').read();"
            "sota_match = re.search(r'Current best score.*?([0-9.eE+-]+)', md);"
            "min_imp_match = re.search(r'Minimum improvement.*?([0-9.eE+-]+)', md);"
            "dir_match = re.search(r'Scoring Direction.*?(MAXIMIZE|MINIMIZE)', md);"
            "sota = float(sota_match.group(1)) if sota_match else None;"
            "min_imp = float(min_imp_match.group(1)) if min_imp_match else 1e-10;"
            "direction = dir_match.group(1) if dir_match else 'MAXIMIZE';"
            "best = results['best_score'];"
            "iter_num = state['iteration'];"
            "if sota is None:"
            "  print('pass: No SOTA yet, any valid score is success');"
            "else:"
            "  success = (best > sota + min_imp) if direction == 'MAXIMIZE' else (best < sota - min_imp);"
            "  if success:"
            "    print('pass: Score improved beyond SOTA');"
            "  elif iter_num >= 2:"  # MVP: only 3 iterations (0, 1, 2)
            "    print('halt: Max iterations reached without improvement');"
            "  else:"
            "    state['iteration'] = iter_num + 1;"
            "    json.dump(state, open('.factory/lumen/state.json', 'w'));"
            "    print('reloop: Need more iterations');"
            "\""
        ),
        reads={".factory/lumen/state.json"},
    )

    # ── Edges ───────────────────────────────────────────────────
    edges = [
        Edge(source="study", target="lumen_context_agent"),
        Edge(source="lumen_context_agent", target="rl_train"),
        Edge(source="rl_train", target="check_gate"),
        Edge(source="check_gate", target="lumen_context_agent", condition=VerdictType.RELOOP),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "lumen"

    return Workflow(
        name="lumen",
        nodes=nodes,
        edges=edges,
        start_node="study",
        terminal=True,
        trigger=trigger,
    )
