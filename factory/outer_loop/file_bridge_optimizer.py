"""An optimizer whose proposals come from a file a graph node wrote.

A reasoning optimizer normally owns its model call. In a decomposed outer-loop
graph the model call belongs to an *agent node*, so the user can watch it and
steer between steps. This optimizer is the other half of that split: the
preceding step writes the gradient to a request file, the agent node answers it,
and this optimizer reads the answer and turns it into candidates using the same
edit rules as :class:`AutoresearchOptimizer`.

Nothing here calls a model, so the Python process never blocks on one and the
proposal step is a first-class, inspectable node.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from factory.outer_loop.autoresearch_optimizer import AutoresearchOptimizer, Edit
from factory.outer_loop.optimizers import (
    OptimizerRegistry,
    Proposal,
    ProposalContext,
)
from factory.workflow.primitives import Workflow

log = structlog.get_logger()

DEFAULT_PROPOSALS = Path(".factory") / "outer_loop" / "proposals.json"


class FileBridgeOptimizer:
    """Turns a proposals file written by an agent node into candidates."""

    name = "file-bridge"

    def __init__(self, proposals_path: Path | None = None) -> None:
        self._path = Path(proposals_path) if proposals_path else DEFAULT_PROPOSALS

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> tuple[str, list[Edit]]:
        """Read the agent node's answer. A missing or broken file means no
        proposals, which is a valid outcome: the node may have declined."""
        if not self._path.exists():
            log.info("file_bridge_no_proposals", path=str(self._path))
            return "", []
        try:
            raw = self._path.read_text()
        except OSError as exc:
            log.warning("file_bridge_unreadable", path=str(self._path), error=str(exc))
            return "", []
        return AutoresearchOptimizer.parse(raw)

    def propose(self, ctx: ProposalContext, count: int) -> list[Proposal]:
        diagnosis, edits = self.read()
        if not edits:
            return []
        sampled = ctx.sample_parent()
        if sampled is None:
            return []
        parent_id, parent_wf = sampled

        surface = {str(k.get("name")): k for k in ctx.knob_surface}
        proposals: list[Proposal] = []
        for edit in edits[: max(1, count)]:
            applied = AutoresearchOptimizer._apply_edit(parent_wf, edit, surface)
            if applied is None:
                continue
            candidate, change = applied
            proposals.append(
                Proposal(
                    workflow=candidate,
                    operator="knob_mutate"
                    if surface.get(edit.knob, {}).get("kind") != "topology"
                    else "param_mutate",
                    target_node=str(surface[edit.knob].get("node_id") or ""),
                    before={"knob": edit.knob, "value": surface[edit.knob].get("value")},
                    after={
                        "knob": edit.knob,
                        "value": edit.value,
                        "prediction": edit.prediction,
                        "confidence": edit.confidence,
                        "diagnosis": diagnosis,
                    },
                    rationale=f"{change}. {edit.rationale}".strip(),
                    parent_id=parent_id,
                    optimizer=self.name,
                )
            )
        log.info("file_bridge_proposed", parsed=len(edits), accepted=len(proposals))
        return proposals


OptimizerRegistry.register(FileBridgeOptimizer.name, FileBridgeOptimizer)
