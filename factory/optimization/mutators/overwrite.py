"""OverwriteMutator — produces GraphMutation objects from overwrite directives.

Absorbs the mutation structure from factory/workflow/overwrite.py and wraps
raw dicts into typed GraphMutation Pydantic models.
"""

from __future__ import annotations

import structlog

from factory.optimization.surface import Surface
from factory.optimization.types import (
    ExecutionResult,
    GraphMutation,
    Patch,
    StepRecord,
)

log = structlog.get_logger()


class OverwriteMutator:
    """Proposes graph mutations based on an overwrite directive.

    Wraps the mutation dict structure from workflow/overwrite.py into typed
    GraphMutation objects. The actual NL interpretation is delegated to the
    strategist agent via factory/workflow/overwrite.py — this mutator provides
    the typed interface for the optimization loop.
    """

    def __init__(self, overwrite_text: str = "") -> None:
        self.overwrite_text = overwrite_text

    def propose(
        self,
        surface: Surface,
        execution_result: ExecutionResult,
        history: list[StepRecord],
    ) -> Patch:
        if not self.overwrite_text or surface.workflow is None:
            log.info("mutator.overwrite.skip", reason="no overwrite text or workflow")
            return Patch(reasoning="No overwrite directive provided")

        mutable = surface.mutable_nodes()
        log.info(
            "mutator.overwrite.propose",
            mutable_nodes=len(mutable),
            overwrite_len=len(self.overwrite_text),
        )
        return Patch(
            graph_mutations=[],
            reasoning=f"OverwriteMutator: directive='{self.overwrite_text[:80]}'",
        )

    @staticmethod
    def mutations_from_dicts(raw: list[dict]) -> list[GraphMutation]:
        """Convert raw mutation dicts (from overwrite.py) to typed GraphMutation objects."""
        return [GraphMutation(**m) for m in raw]
