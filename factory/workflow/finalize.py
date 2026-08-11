"""Finalize-standalone workflow.

Runs the decomposed finalize pipeline (precheck hard gate → experiment
finalize → archivist → spec-update) as a standalone mode.  This is the
"finalize factory": the keep/revert decision with its non-overridable
precheck gate.  Triggered via `factory workflow run finalize-standalone`
or `factory ceo /path --mode finalize-standalone`.

Requires $EXP_ID, $VERDICT (keep/revert/error), and $HYPOTHESIS to be
provided in the environment — the CEO substitutes them in skill mode.
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.definitions import FinalizeConfig, _finalize_subgraph
from factory.workflow.primitives import Edge, Workflow

meta = {
    "name": "finalize-standalone",
    "description": (
        "Standalone finalize factory — the precheck hard gate (non-overridable), "
        "then closes the experiment with a keep/revert verdict via `factory finalize`, "
        "archives results, and updates SPEC.md. Expects $EXP_ID, $VERDICT, "
        "$HYPOTHESIS in the environment."
    ),
}


def workflow() -> Workflow:
    """Build the standalone finalize workflow."""
    f_nodes, f_edges = _finalize_subgraph(config=FinalizeConfig(mode="experiment"))

    # Standalone boundary: the finalize step's reads (QA reviews) have no
    # predecessor here — clear them (validation requires reads ⊆ pred writes).
    f_nodes["finalize"] = f_nodes["finalize"].model_copy(update={"reads": set()})

    nodes: dict[str, Any] = {**f_nodes}
    edges: list[Edge] = [*f_edges]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "finalize-standalone"

    return Workflow(
        name="finalize-standalone",
        nodes=nodes,
        edges=edges,
        start_node="gate_precheck",
        trigger=trigger,
    )
