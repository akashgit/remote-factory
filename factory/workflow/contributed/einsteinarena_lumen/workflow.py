"""EinsteinArena-LUMEN RL Workflow.

LUMEN: Learning-based Universal Modeling and Evolution eNgine
RL training system for EinsteinArena scientific discovery benchmark.

All nodes read configuration from {run_dir}/config.json, where {run_dir}
is created by the workflow executor (e.g. .factory/einsteinarena-lumen/run_YYYYMMDD-HHMMSS/).

Environment: Uses uv virtual environment for Python execution.
Default path: factory/lumen/.venv/bin/python
Override via: LUMEN_PYTHON environment variable
"""

import os
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
    "name": "einsteinarena-lumen",
    "description": "EinsteinArena-LUMEN: RL training workflow for EinsteinArena scientific discovery benchmark",
}

# Run config/state paths — resolved by executor via {run_dir} template
_CFG = "{run_dir}/config.json"
_STATE = "{run_dir}/state.json"

# Lumen Python interpreter path (uv venv in factory/lumen/.venv)
# Can be overridden via LUMEN_PYTHON environment variable
# Default: {remote-factory-root}/factory/lumen/.venv/bin/python
def _get_lumen_python() -> str:
    if "LUMEN_PYTHON" in os.environ:
        return os.environ["LUMEN_PYTHON"]
    # This file is in: remote-factory/factory/workflow/contributed/einsteinarena_lumen/workflow.py
    # .venv is in:     remote-factory/factory/lumen/.venv/bin/python
    import pathlib
    # __file__ -> workflow.py
    # .parent -> einsteinarena_lumen/
    # .parent.parent -> contributed/
    # .parent.parent.parent -> workflow/
    # .parent.parent.parent.parent -> factory/
    # .parent.parent.parent.parent.parent -> remote-factory/
    remote_factory_root = pathlib.Path(__file__).parent.parent.parent.parent.parent
    return str(remote_factory_root / "factory" / "lumen" / ".venv" / "bin" / "python")

def _get_lumen_root() -> str:
    """Get the absolute path to the lumen directory."""
    if "LUMEN_ROOT" in os.environ:
        return os.environ["LUMEN_ROOT"]
    # This file is in: remote-factory/factory/workflow/contributed/einsteinarena_lumen/workflow.py
    # Lumen root is:   remote-factory/factory/lumen/
    import pathlib
    remote_factory_root = pathlib.Path(__file__).parent.parent.parent.parent.parent
    return str(remote_factory_root / "factory" / "lumen")

_LUMEN_PYTHON = _get_lumen_python()
_LUMEN_ROOT = _get_lumen_root()

# Get remote-factory root for PYTHONPATH (parent of factory/)
import pathlib
_REMOTE_FACTORY_ROOT = str(pathlib.Path(_LUMEN_ROOT).parent.parent)


def workflow() -> Workflow:
    """Build the EinsteinArena-LUMEN workflow graph.

    All configuration is read from config.json in the project path.
    The preflight node automatically discovers the config file.
    """
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 0: Setup ─────────────────────────────────────────
    # Preflight (checks uv venv, GPUs, run dir, resolved config) + SOTA update.
    nodes["setup"] = FnNode(
        id="setup",
        command=(
            "cd {project_path} && "
            f"LUMEN_PYTHON={_LUMEN_PYTHON} "
            f"python3 {_LUMEN_ROOT}/preflight.py --project-path {{project_path}} --run-dir {{run_dir}} && "
            f"TASK=$({_LUMEN_PYTHON} -c \""
            "import json, sys; print(json.load(open(sys.argv[1]))['task_name'])"
            '" {run_dir}/config.json) && '
            f"(cd {{project_path}}/.. && {_LUMEN_PYTHON} tools/add_sota_to_instruction.py $TASK)"
        ),
        writes={"{run_dir}/config.json", "{run_dir}/state.json"},
        transcript_dir="{run_dir}/logs",
    )

    # ── Node 1: Config Gate ────────────────────────────────────
    # Interactive: CEO presents the resolved config to the user for approval.
    # Headless (auto_approve=True): auto-proceeds.
    nodes["config_gate"] = GateNode(
        id="config_gate",
        evaluator_type="user",
        reads={_CFG},
    )

    # ── Node 2: Lumen Context Agent ────────────────────────────
    nodes["lumen_context_agent"] = AgentNode(
        id="lumen_context_agent",
        role=AgentRole.LUMEN_CONTEXT_AGENT,
        model="opus",
        timeout=1800,
        disallowed_tools=["WebFetch", "WebSearch"],
        safe_mode=True,
        effort="max",
        prompt_template=(
            "You are the Lumen Context Agent. Generate 8 optimization prompts.\n\n"
            f"Read the run config at {_CFG} to find task_name and iteration.\n"
            "Read: <task_name>/instruction.md\n"
            f"Read: {_STATE} for current iteration.\n\n"
            "If iteration > 0, read the previous iteration's evaluation_results.json "
            "from {run_dir}/iteration_<prev>/evaluation_results.json\n\n"
            "Output: {run_dir}/iteration_<current>/prompts.json\n\n"
            "Follow the format specified in factory/agents/prompts/lumen_context_agent.md"
        ),
        reads={_CFG, _STATE},
        writes={"{run_dir}/iteration_*/prompts.json"},
        transcript_dir="{run_dir}/logs",
    )

    # ── Node 3: RL Training ─────────────────────────────────────
    # CRITICAL: Must run in lumen env (needs numpy, verl, vLLM)
    nodes["rl_train"] = FnNode(
        id="rl_train",
        command=(
            "cd {project_path} && "
            f"PYTHONPATH={_REMOTE_FACTORY_ROOT}:$PYTHONPATH "
            f"{_LUMEN_PYTHON} {_LUMEN_ROOT}/train.py "
            f"--config {_CFG}"
        ),
        reads={_CFG, _STATE, "{run_dir}/iteration_*/prompts.json"},
        writes={
            "{run_dir}/iteration_*/sm_rollouts.jsonl",
        },
        transcript_dir="{run_dir}/logs",
    )

    # ── Node 4: Eval Stats ──────────────────────────────────────
    # Aggregate sm_rollouts + fm_rollouts (optional) into unified evaluation_results.json
    # NOTE: fm_rollouts.jsonl is optional — eval_stats.py checks for it internally
    nodes["eval_stats"] = FnNode(
        id="eval_stats",
        command=f"cd {{project_path}} && python3 {_LUMEN_ROOT}/eval_stats.py --run-dir {{run_dir}}",
        reads={
            _CFG,
            _STATE,
            "{run_dir}/iteration_*/sm_rollouts.jsonl",
        },
        writes={
            "{run_dir}/iteration_*/evaluation_results.json",
        },
        transcript_dir="{run_dir}/logs",
    )

    # ── Node 5: Check Gate ──────────────────────────────────────
    # Uses only stdlib - can run in any Python
    nodes["check_gate"] = GateNode(
        id="check_gate",
        evaluator_type="fn",
        evaluator_command=f"cd {{project_path}} && python3 {_LUMEN_ROOT}/check_gate.py --run-dir {{run_dir}}",
        reads={_CFG, _STATE},
    )

    # ── Node 6: Finalize ───────────────────────────────────────
    # Clean up checkpoint if SOTA was not beaten.
    nodes["finalize"] = FnNode(
        id="finalize",
        command=f"cd {{project_path}} && python3 {_LUMEN_ROOT}/finalize.py --run-dir {{run_dir}}",
        reads={_CFG, _STATE},
        transcript_dir="{run_dir}/logs",
    )

    # ── Edges ───────────────────────────────────────────────────
    edges = [
        Edge(source="setup", target="config_gate"),
        Edge(source="config_gate", target="lumen_context_agent"),
        Edge(source="lumen_context_agent", target="rl_train"),
        Edge(source="rl_train", target="eval_stats"),
        Edge(source="eval_stats", target="check_gate"),
        Edge(source="check_gate", target="lumen_context_agent", condition=VerdictType.RELOOP),
        Edge(source="check_gate", target="finalize"),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "einsteinarena-lumen"

    return Workflow(
        name="einsteinarena-lumen",
        nodes=nodes,
        edges=edges,
        start_node="setup",
        terminal=True,
        trigger=trigger,
    )
