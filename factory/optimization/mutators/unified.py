"""UnifiedMutator — composes SkillOptMutator + OverwriteMutator."""

from __future__ import annotations

import structlog

from factory.optimization.mutators.overwrite import OverwriteMutator
from factory.optimization.mutators.skillopt import SkillOptMutator
from factory.optimization.surface import Surface
from factory.optimization.types import ExecutionResult, Patch, StepRecord

log = structlog.get_logger()


class UnifiedMutator:
    """Composes prompt-slot edits and graph mutations, respecting frozen_nodes."""

    def __init__(
        self,
        skillopt: SkillOptMutator | None = None,
        overwrite: OverwriteMutator | None = None,
    ) -> None:
        self.skillopt = skillopt or SkillOptMutator()
        self.overwrite = overwrite or OverwriteMutator()

    def propose(
        self,
        surface: Surface,
        execution_result: ExecutionResult,
        history: list[StepRecord],
    ) -> Patch:
        prompt_patch = self.skillopt.propose(surface, execution_result, history)
        graph_patch = self.overwrite.propose(surface, execution_result, history)

        frozen = surface.frozen_nodes
        filtered_mutations = [
            m for m in graph_patch.graph_mutations
            if m.node_id is None or m.node_id not in frozen
        ]

        combined = Patch(
            prompt_edits=prompt_patch.prompt_edits,
            graph_mutations=filtered_mutations,
            reasoning=f"Unified: {prompt_patch.reasoning} | {graph_patch.reasoning}",
        )
        log.info(
            "mutator.unified.propose",
            prompt_edits=len(combined.prompt_edits),
            graph_mutations=len(combined.graph_mutations),
        )
        return combined
