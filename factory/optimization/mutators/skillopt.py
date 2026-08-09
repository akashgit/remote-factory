"""SkillOptMutator — proposes prompt-slot edits.

Stub: the full reflect/merge/clip pipeline is deferred to when PR #1013 merges.
This implementation reads prompt_slots from the Surface and returns an empty Patch.
"""

from __future__ import annotations

import structlog

from factory.optimization.surface import Surface
from factory.optimization.types import ExecutionResult, Patch, StepRecord

log = structlog.get_logger()


class SkillOptMutator:
    """Proposes SlotEdit changes to prompt slots on the Surface.

    The full pipeline (reflect → aggregate → clip) will be wired
    when the SkillOpt PR merges. For now, returns an empty patch.
    """

    def propose(
        self,
        surface: Surface,
        execution_result: ExecutionResult,
        history: list[StepRecord],
    ) -> Patch:
        slots = surface.mutable_prompt_slots()
        log.info("mutator.skillopt.propose", num_slots=len(slots))
        return Patch(reasoning="SkillOptMutator stub — no edits proposed")
