"""A reasoning optimizer: the model reads the gradient and proposes the edit.

This is the optimizer that makes the outer loop more than random search. It
receives the same :class:`~factory.outer_loop.optimizers.ProposalContext` as the
baseline — the trace facts of recent candidates, the reflection report, and the
declared ``OptKnob`` surface — and returns proposals that name a specific knob
and value, with a stated reason and a prediction.

Two properties matter more than the prompt:

- **The model proposes values, never free-form graphs.** Every proposal is a
  value for a declared knob, so it can be validated against that knob's kind and
  bounds before anything is evaluated (see ``expandable`` handling in
  :meth:`AutoresearchOptimizer._apply_edit`).
- **The proposal records what it expected.** ``prediction`` and ``confidence``
  travel with the proposal into the trace, so a later pass can score the
  optimizer itself rather than only the candidates it produced.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import structlog

from factory.outer_loop.optimizers import (
    ModelClient,
    OptimizerRegistry,
    Proposal,
    ProposalContext,
)
from factory.outer_loop.topology import TopologyRegistry, apply_topology
from factory.workflow.primitives import Workflow

log = structlog.get_logger()

_SYSTEM = (
    "You are the optimizer of a search loop. You are given the results of recent "
    "candidate runs and the knobs you are allowed to move. Propose the next "
    "candidates by choosing knob values. Reason from the evidence: say what the "
    "results show, what you expect your change to do, and how wrong you could be. "
    "Answer with JSON only."
)

_SCHEMA = """{
  "diagnosis": "<what the evidence shows is limiting the score>",
  "proposals": [
    {
      "knob": "<knob name from the surface>",
      "value": "<new value, numeric or string>",
      "rationale": "<why this value, citing the evidence>",
      "prediction": "<expected effect on the score>",
      "confidence": <0.0-1.0>
    }
  ]
}"""


@dataclass
class Edit:
    """One parsed proposal, before it is applied to a workflow."""

    knob: str
    value: Any
    rationale: str = ""
    prediction: str = ""
    confidence: float = 0.0


class AutoresearchOptimizer:
    """Proposes knob values by reasoning over the trace facts."""

    name = "autoresearch"

    def __init__(
        self,
        client: ModelClient,
        *,
        max_traces: int = 8,
        max_proposals: int = 4,
    ) -> None:
        self._client = client
        self._max_traces = max_traces
        self._max_proposals = max_proposals
        #: The last diagnosis the model produced, for the run's artifacts.
        self.last_diagnosis: str = ""

    # ── prompt construction ────────────────────────────────────────────

    def _summarize_traces(self, traces: list[dict[str, object]]) -> str:
        """Render recent candidates compactly: what was tried, what happened.

        Each line carries the path of that candidate's full trace summary, whose
        own links point at the raw artifacts. The caller is a tool-using agent,
        so a compact digest plus a way to drill in beats pasting every artifact
        into the prompt.
        """
        if not traces:
            return "(no previous runs yet — this is the first generation)"
        lines: list[str] = []
        for facts in traces[-self._max_traces :]:
            proposal = facts.get("proposal") or {}
            outcome = facts.get("outcome") or {}
            shape = facts.get("shape") or {}
            paths = facts.get("paths") or {}
            line = (
                f"- candidate {facts.get('key')}: operator={proposal.get('operator')} "
                f"target={proposal.get('target_node')} "
                f"knobs={json.dumps(shape.get('knob_values', {}))} "
                f"score={outcome.get('score')} parent={outcome.get('parent_score')} "
                f"verdict={outcome.get('verdict')}"
            )
            lines.append(line)
            for note in (facts.get("observations") or [])[:3]:
                lines.append(f"    - {note}")
            if paths.get("summary"):
                lines.append(f"    - full trace (links to raw artifacts): {paths['summary']}")
        return "\n".join(lines)

    def _surface_text(self, surface: list[dict[str, object]]) -> str:
        if not surface:
            return "(no knobs declared)"
        lines = []
        for knob in surface:
            expandable = " (expandable: values beyond bounds are allowed)" if knob.get(
                "expandable"
            ) else ""
            lines.append(
                f"- {knob.get('name')}: kind={knob.get('kind')} value={knob.get('value')} "
                f"bounds={knob.get('bounds')} default={knob.get('default')}{expandable}\n"
                f"    {knob.get('description', '')}"
            )
        return "\n".join(lines)

    def build_prompt(self, ctx: ProposalContext) -> str:
        """Assemble the gradient the model reasons over."""
        reflection = ctx.reflection
        patterns: list[str] = []
        if reflection is not None:
            patterns = [
                *(f"failure: {p}" for p in reflection.failure_patterns[:6]),
                *(f"success: {p}" for p in reflection.success_patterns[:6]),
                *(
                    f"suggestion: {s}"
                    for s in (
                        list(reflection.mutation_suggestions)
                        + list(reflection.structural_recommendations)
                    )[:6]
                ),
            ]
        return "\n".join(
            [
                f"# Generation {ctx.generation}",
                "",
                "## Recent candidate runs (oldest first)",
                self._summarize_traces(list(ctx.traces)),
                "",
                "Read any trace summary above for detail: each one links to the "
                "raw artifacts of that run (scores, verdicts, errors, generated "
                "code). Do not guess at numbers you can read.",
                "",
                "## Mechanical contrastive reflection",
                "\n".join(patterns) if patterns else "(none)",
                "",
                "## Knobs you may move",
                self._surface_text(list(ctx.knob_surface)),
                "",
                f"## Your task",
                f"Propose up to {self._max_proposals} candidates for the next generation. "
                "Prefer a small number of well-argued changes over many guesses. "
                "Vary the knob you move so the search learns which knob matters. "
                "If the evidence says a knob is saturated, either push it past its "
                "bounds (only if marked expandable) or leave it and move another.",
                "",
                "Answer with JSON exactly in this shape:",
                _SCHEMA,
            ]
        )

    # ── response parsing ───────────────────────────────────────────────

    @staticmethod
    def parse(raw: str) -> tuple[str, list[Edit]]:
        """Extract the diagnosis and edits from a model response.

        Tolerates the model wrapping JSON in prose or a fenced block, which is
        common enough that rejecting it would throw away otherwise good
        proposals.
        """
        block = raw.strip()
        fenced = re.search(r"```(?:json)?\s*(.+?)```", block, re.DOTALL)
        if fenced:
            block = fenced.group(1).strip()
        if not block.startswith("{"):
            brace = block.find("{")
            if brace >= 0:
                block = block[brace:]
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            log.warning("autoresearch_unparseable_response", chars=len(raw))
            return "", []
        if not isinstance(data, dict):
            return "", []

        edits: list[Edit] = []
        for item in data.get("proposals") or []:
            if not isinstance(item, dict) or "knob" not in item or "value" not in item:
                continue
            try:
                confidence = float(item.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            edits.append(
                Edit(
                    knob=str(item["knob"]),
                    value=item["value"],
                    rationale=str(item.get("rationale", "")),
                    prediction=str(item.get("prediction", "")),
                    confidence=confidence,
                )
            )
        return str(data.get("diagnosis", "")), edits

    # ── applying an edit to a candidate ────────────────────────────────

    @staticmethod
    def _apply_edit(
        parent: Workflow,
        edit: Edit,
        surface: dict[str, dict[str, object]],
    ) -> tuple[Workflow, str] | None:
        """Return the candidate this edit produces, or None if it is not legal.

        A proposal is rejected rather than repaired when it names an undeclared
        knob or a value outside a closed domain: silently clamping would make the
        recorded rationale a lie about what was actually tried.
        """
        knob = surface.get(edit.knob)
        if knob is None:
            log.warning("autoresearch_unknown_knob", knob=edit.knob)
            return None

        if knob.get("kind") == "topology":
            return AutoresearchOptimizer._apply_topology_edit(parent, edit, knob)

        bounds = list(knob.get("bounds") or [])
        value = edit.value
        if isinstance(value, str) and bounds and all(isinstance(b, (int, float)) for b in bounds):
            try:
                value = float(value)
            except ValueError:
                return None
        if bounds and value not in bounds and not knob.get("expandable"):
            log.info(
                "autoresearch_out_of_bounds",
                knob=edit.knob,
                value=value,
                bounds=bounds,
            )
            return None

        candidate = Workflow.from_dict(parent.to_dict())
        candidate.knob_values = dict(parent.knob_values)
        candidate.knob_values[edit.knob] = value
        if value not in bounds:
            # An expanded value becomes part of the domain so the next
            # generation can select and mutate it like any other.
            candidate.knob_bounds = dict(parent.knob_bounds)
            candidate.knob_bounds[edit.knob] = [*bounds, value]

        # A prompt knob only means something if the prompt reaches the node it
        # names; writing knob_values alone leaves the mode behaving unchanged.
        if knob.get("kind") == "prompt":
            node_id = str(knob.get("node_id") or "")
            if node_id and not AutoresearchOptimizer._set_node_prompt(candidate, node_id, value):
                log.info("autoresearch_prompt_node_missing", knob=edit.knob, node_id=node_id)
                return None
        return candidate, f"{edit.knob}: {knob.get('value')} -> {value}"

    @staticmethod
    def _apply_topology_edit(
        parent: Workflow,
        edit: Edit,
        knob: dict[str, object],
    ) -> tuple[Workflow, str] | None:
        """Run a named structural operation on the knob's node.

        The value must be a known operation. A closed knob restricts it to the
        declared bounds; an expandable one may name any registered operation,
        which is what makes an expandable topology knob meaningfully broader than
        a closed one.
        """
        node_id = str(knob.get("node_id") or "")
        operation = str(edit.value)
        bounds = [str(b) for b in (knob.get("bounds") or [])]
        if not knob.get("expandable") and bounds and operation not in bounds:
            log.info(
                "autoresearch_topology_out_of_bounds",
                knob=edit.knob,
                operation=operation,
                bounds=bounds,
            )
            return None
        if operation not in TopologyRegistry.names():
            log.warning(
                "autoresearch_topology_unknown",
                knob=edit.knob,
                operation=operation,
                known=TopologyRegistry.names(),
            )
            return None

        applied = apply_topology(parent, node_id, operation)
        if applied is None:
            return None
        candidate, record = applied
        candidate.knob_values = dict(parent.knob_values)
        candidate.knob_values[edit.knob] = operation
        return candidate, f"{edit.knob} on {node_id}: {operation} ({record.rationale})"

    @staticmethod
    def _set_node_prompt(workflow: Workflow, node_id: str, value: Any) -> bool:
        """Write a prompt value onto the node it belongs to.

        The workflow carries prompts in two places: ``knob_values`` (which
        survives compile round-trips) and the node itself (which is what the
        runner actually reads). Both must agree or the knob is decorative.
        """
        node = workflow.nodes.get(node_id)
        if node is None:
            return False
        prompt = str(value)
        for field_name in ("prompt_template", "system_prompt"):
            if hasattr(node, field_name):
                setattr(node, field_name, prompt)
                return True
        return False

    # ── the Optimizer interface ────────────────────────────────────────

    def propose(self, ctx: ProposalContext, count: int) -> list[Proposal]:
        sampled = ctx.sample_parent()
        if sampled is None:
            return []
        parent_id, parent_wf = sampled

        prompt = self.build_prompt(ctx) + (
            f"\n\n(previous best score: {ctx.archive_stats.get('best_score')})\n"
        )
        try:
            raw = self._client.complete(prompt, system=_SYSTEM)
        except Exception as exc:  # noqa: BLE001 - a model failure must not kill the run
            log.warning("autoresearch_model_failed", error=str(exc))
            return []

        diagnosis, edits = self.parse(raw)
        self.last_diagnosis = diagnosis

        surface = {str(k.get("name")): k for k in ctx.knob_surface}
        proposals: list[Proposal] = []
        for edit in edits[: max(1, min(self._max_proposals, count))]:
            applied = self._apply_edit(parent_wf, edit, surface)
            if applied is None:
                continue
            candidate, change = applied
            proposals.append(
                Proposal(
                    workflow=candidate,
                    operator="knob_mutate",
                    target_node=str(surface[edit.knob].get("node_id") or ""),
                    before={"knob": edit.knob, "value": surface[edit.knob].get("value")},
                    after={
                        "knob": edit.knob,
                        "value": edit.value,
                        "prediction": edit.prediction,
                        "confidence": edit.confidence,
                    },
                    rationale=f"{change}. {edit.rationale}".strip(),
                    parent_id=parent_id,
                    optimizer=self.name,
                )
            )
        log.info(
            "autoresearch_proposed",
            generation=ctx.generation,
            parsed=len(edits),
            accepted=len(proposals),
        )
        return proposals


OptimizerRegistry.register(AutoresearchOptimizer.name, AutoresearchOptimizer)
