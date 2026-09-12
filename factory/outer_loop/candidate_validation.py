"""Pre-evaluation validation: reject a candidate before spending a run on it.

Structural validation is not enough. ``remove_node`` maintains the data-flow
invariant as it deletes: removing the node that writes ``candidate.py`` also
strips ``candidate.py`` from its consumers' ``reads``, so the resulting graph is
internally consistent and passes ``validate_workflow`` while being a mode that
can never produce a candidate. The only way to see that is to compare the
candidate against the contract the *seed* mode established.

So the contract is captured once, from the seed:

- **Required artifacts** — every file the seed both produced and consumed is an
  interface between its nodes. A candidate that stops producing one has broken
  the mode, however tidy its declarations look.
- **Knob consumers** — every declared knob must still reach the node its spec
  names. A knob whose node is gone is a declaration that changes nothing.

A candidate failing either check is rejected before evaluation and the reason is
recorded, so the rejection surface is inspectable rather than silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from factory.workflow.primitives import Workflow
from factory.workflow.validation import validate_workflow

log = structlog.get_logger()


@dataclass
class ModeContract:
    """What a mode must keep doing, captured from the seed that defines it."""

    required_artifacts: set[str] = field(default_factory=set)
    required_nodes: set[str] = field(default_factory=set)
    knob_nodes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_workflow(cls, seed: Workflow) -> "ModeContract":
        """Derive the contract from a known-good mode."""
        written: dict[str, list[str]] = {}
        read: set[str] = set()
        knob_nodes: dict[str, str] = {}
        for node_id, node in seed.nodes.items():
            for artifact in node.writes:
                written.setdefault(artifact, []).append(node_id)
            read |= set(node.reads)
        for name, spec in (seed.knob_specs or {}).items():
            node_id = spec.get("node_id")
            if node_id:
                knob_nodes[name] = str(node_id)

        # An artifact that is both produced and consumed is an interface between
        # nodes: losing its producer breaks the mode even if the graph stays tidy.
        required = {artifact for artifact in written if artifact in read}
        # Nodes that own a required artifact are the ones that must survive.
        required_nodes = {
            node_id
            for artifact in required
            for node_id in written.get(artifact, [])
        }
        return cls(
            required_artifacts=required,
            required_nodes=required_nodes,
            knob_nodes=knob_nodes,
        )


class CandidateValidator:
    """Checks a proposed candidate against the mode contract before evaluation."""

    def __init__(self, contract: ModeContract) -> None:
        self._contract = contract

    @property
    def contract(self) -> ModeContract:
        return self._contract

    def issues(self, candidate: Workflow) -> list[str]:
        """Return the reasons this candidate must not be evaluated.

        Empty means the candidate is accepted. Each reason is a short, specific
        sentence, because these are surfaced to the user as rejection reasons.
        """
        reasons: list[str] = []

        produced: set[str] = set()
        for node in candidate.nodes.values():
            produced |= set(node.writes)
        lost = sorted(self._contract.required_artifacts - produced)
        if lost:
            reasons.append(f"no longer produces {', '.join(lost)}")

        missing_nodes = sorted(self._contract.required_nodes - set(candidate.nodes))
        if missing_nodes:
            reasons.append(f"removed required node(s) {', '.join(missing_nodes)}")

        for knob, node_id in self._contract.knob_nodes.items():
            if knob in candidate.knob_values and node_id not in candidate.nodes:
                reasons.append(f"knob '{knob}' has no consumer: node '{node_id}' is gone")

        structural = validate_workflow(candidate)
        if structural:
            reasons.append(f"invalid graph: {structural[0]}")

        if reasons:
            log.info(
                "candidate_rejected",
                name=candidate.name,
                reasons=reasons[:3],
            )
        return reasons
