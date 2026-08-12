"""Einstein Arena RL Workflow - MVP version."""

from typing import Any

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
    "name": "einsteinarena",
    "description": "Einstein Arena RL training workflow (MVP)",
}


def workflow() -> Workflow:
    """Build the Einstein Arena workflow graph."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 1: Study ──────────────────────────────────────────
    nodes["study"] = FnNode(
        id="study",
        command=(
            "mkdir -p {project_path}/.factory/rl && "
            "python3 benchmarks/tools/add_sota_to_instruction.py {task_name} || true && "
            'echo \'{"iteration": 0, "best_score": null}\' > {project_path}/.factory/rl/state.json && '
            "echo 'Study complete'"
        ),
        writes={".factory/rl/state.json"},
    )

    # ── Node 2: Lumen Context Agent ────────────────────────────
    nodes["lumen_context_agent"] = AgentNode(
        id="lumen_context_agent",
        role=AgentRole.LUMEN_CONTEXT_AGENT,
        model="opus",
        timeout=1800,
        prompt_template=(
            "You are the Lumen Context Agent. Generate 8 optimization prompts.\n\n"
            "Read: benchmarks/einsteinarena-harbor/{task_name}/instruction.md\n"
            "Read: .factory/rl/state.json\n\n"
            "If iteration > 0, read: .factory/rl/iteration_{{prev_iteration}}/evaluation_results.json\n\n"
            "Output: .factory/rl/iteration_{{current_iteration}}/prompts.json\n\n"
            "Follow the format specified in factory/agents/prompts/lumen_context_agent.md"
        ),
        reads={
            "benchmarks/einsteinarena-harbor/{task_name}/instruction.md",
            ".factory/rl/state.json",
        },
        writes={".factory/rl/iteration_{current_iteration}/prompts.json"},
    )

    # ── Node 3: RL Training ─────────────────────────────────────
    nodes["rl_train"] = FnNode(
        id="rl_train",
        command=(
            "cd {project_path} && "
            'ITER=$(python3 -c "import json; print(json.load(open(\'.factory/rl/state.json\'))[\'iteration\'])") && '
            "python3 -m factory.rl.train "
            "--task {task_name} "
            "--task-dir benchmarks/einsteinarena-harbor/{task_name} "
            "--project-path {project_path} "
            "--iteration $ITER "
            "--num-rollouts-per-prompt 8 "
            "--mock"
        ),
        reads={".factory/rl/state.json", ".factory/rl/iteration_{current_iteration}/prompts.json"},
        writes={
            ".factory/rl/iteration_{current_iteration}/rollouts.jsonl",
            ".factory/rl/iteration_{current_iteration}/evaluation_results.json",
        },
    )

    # ── Node 4: Check Gate ──────────────────────────────────────
    nodes["check_gate"] = GateNode(
        id="check_gate",
        command=(
            "python3 -c \""
            "import json, sys, re;"
            "results = json.load(open('.factory/rl/iteration_' + str(json.load(open('.factory/rl/state.json'))['iteration']) + '/evaluation_results.json'));"
            "state = json.load(open('.factory/rl/state.json'));"
            "# Read SOTA from instruction.md;"
            "md = open('benchmarks/einsteinarena-harbor/{task_name}/instruction.md').read();"
            "sota_match = re.search(r'Current best score.*?([0-9.eE+-]+)', md);"
            "min_imp_match = re.search(r'Minimum improvement.*?([0-9.eE+-]+)', md);"
            "dir_match = re.search(r'Scoring Direction.*?(MAXIMIZE|MINIMIZE)', md);"
            "sota = float(sota_match.group(1)) if sota_match else None;"
            "min_imp = float(min_imp_match.group(1)) if min_imp_match else 1e-10;"
            "direction = dir_match.group(1) if dir_match else 'MAXIMIZE';"
            "best = results['best_score'];"
            "iter_num = state['iteration'];"
            "# Check success;"
            "if sota is None:"
            "  sys.exit(0);"  # No SOTA yet, any valid score is success
            "success = (best > sota + min_imp) if direction == 'MAXIMIZE' else (best < sota - min_imp);"
            "if success:"
            "  sys.exit(0);"  # PROCEED
            "elif iter_num >= 2:"  # MVP: only 3 iterations (0, 1, 2)
            "  sys.exit(2);"  # FAIL
            "else:"
            "  state['iteration'] = iter_num + 1;"
            "  json.dump(state, open('.factory/rl/state.json', 'w'));"
            "  sys.exit(1)"  # RELOOP
            "\""
        ),
        verdicts={
            VerdictType.PROCEED: 0,
            VerdictType.RELOOP: 1,
            VerdictType.FAIL: 2,
        },
    )

    # ── Edges ───────────────────────────────────────────────────
    edges = [
        Edge(source="study", target="lumen_context_agent"),
        Edge(source="lumen_context_agent", target="rl_train"),
        Edge(source="rl_train", target="check_gate"),
        Edge(source="check_gate", target="lumen_context_agent", verdict=VerdictType.RELOOP),
    ]

    return Workflow(
        name="einsteinarena",
        nodes=nodes,
        edges=edges,
        start_node="study",
        terminal=False,
    )
