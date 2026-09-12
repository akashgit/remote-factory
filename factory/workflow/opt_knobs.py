"""What a mode lets you change, derived from the mode itself.

The outer loop is an optimizer, so a mode has to answer the question a
``nn.Module`` answers with ``parameters()``: *what in me is optimizable?* Today
that surface is hand-written into the emitter, which means a new mode exposes
whatever the author remembered to type, and nothing checks that the declared
knobs are honoured by anything.

This module derives the surface from the graph. Every node field that is a
legitimate thing to tune becomes an :class:`OptKnob`, so the answer comes from
the mode rather than from a literal.

A declared knob is not automatically an optimizable one, and the difference is
the whole point. PyTorch cannot express "a parameter the optimizer may move but
that no forward pass reads" — the graph can, and we shipped one: ``temperature``
was a declared knob with no consumer anywhere on the science path, so it moved
the outer loop's surrogate score while changing nothing about the mode. Every
field therefore carries its consumption status:

- ``consumed``   — the runtime reads it; advertised to the optimizer.
- ``dead``       — nothing reads it; reported as a defect, never advertised.
- ``unverified`` — not checked one way or the other; reported, not advertised.

Only ``consumed`` fields are offered, so the surface cannot promise a knob that
does nothing. The other two are surfaced for the same reason the run refuses a
candidate whose knob lost its node: a knob that lies is worse than a knob that
is absent.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from factory.workflow.package import OptKnob
    from factory.workflow.primitives import Workflow


@dataclass(frozen=True)
class FieldSpec:
    """How a node field behaves as an optimizable knob."""

    kind: str
    """One of the ``OptKnob`` kinds: prompt, model, threshold, topology."""

    bounds: list[Any] = field(default_factory=list)
    expandable: bool = True
    status: str = "unverified"
    """``consumed``, ``dead`` or ``unverified``."""

    consumed_by: str = ""
    description: str = ""


#: Node fields that can be tuned, with what the runtime does with them.
#:
#: ``consumed`` is a claim about the runtime and has to be maintained as the
#: runtime changes; a field whose consumer is removed must move to ``dead`` or
#: the mode starts advertising a knob that does nothing again.
FIELD_SPECS: dict[str, FieldSpec] = {
    # ── prompts: read when the node's work is dispatched ──
    "prompt_template": FieldSpec(
        kind="prompt", status="consumed", expandable=True,
        consumed_by="the agent spawned for an AgentNode",
        description="the instructions the agent is given",
    ),
    "system_prompt": FieldSpec(
        kind="prompt", status="consumed", expandable=True,
        consumed_by="the request built for an LLMNode",
        description="the system instructions for the model call",
    ),
    "instance_prompt": FieldSpec(
        kind="prompt", status="consumed", expandable=True,
        consumed_by="the request built for an LLMNode",
        description="the per-call instructions for the model call",
    ),
    "gate_prompt": FieldSpec(
        kind="prompt", status="consumed", expandable=True,
        consumed_by="the evaluator agent for a GateNode",
        description="how a gate decides",
    ),
    # ── model choice ──
    "model": FieldSpec(
        kind="model", status="consumed", expandable=True,
        consumed_by="the request built for the node",
        description="which model the node runs",
    ),
    "provider": FieldSpec(
        kind="model", status="consumed", expandable=True,
        consumed_by="the client the node's request is sent through",
        description="which provider serves the model",
    ),
    # ── thresholds with a real effect ──
    "max_iterations": FieldSpec(
        kind="threshold", bounds=[1, 2, 4, 8, 16], status="consumed",
        consumed_by="the reloop cap a gate enforces",
        description="how many times a gate may reloop",
    ),
    "timeout": FieldSpec(
        kind="threshold", bounds=[60, 300, 900, 1800, 3600], status="consumed",
        consumed_by="the command runner's per-node timeout",
        description="wall-clock ceiling for the node's work",
    ),
    "temperature": FieldSpec(
        kind="threshold", bounds=[0.0, 0.3, 0.5, 0.7, 0.9, 1.0], status="dead",
        consumed_by="",
        description="sampling temperature — DECLARED BUT NOT READ on the science "
                    "path: the spine projects it onto the node view and no consumer "
                    "reads it, so moving it changes nothing about the mode",
    ),
    # ── not checked; reported so the omission is visible rather than silent ──
    "max_tokens": FieldSpec(kind="threshold", status="unverified",
                            description="completion length ceiling"),
    "max_turns": FieldSpec(kind="threshold", status="unverified",
                           description="tool-use turn ceiling"),
    "stop_sequences": FieldSpec(kind="threshold", status="unverified",
                                description="stop sequences for the model call"),
    "tool_choice": FieldSpec(kind="model", status="unverified",
                             description="how the model must use tools"),
    "tools": FieldSpec(kind="topology", status="unverified",
                       description="the node's tool surface"),
}

#: Fields whose value is code or identity rather than a tunable. Changing these
#: is editing the mode, not tuning it, so they are never knobs.
NOT_KNOBS = frozenset({"id", "reads", "writes", "blocking", "command", "callable_name",
                       "notes", "post_checks", "evaluator_command", "evaluator_type",
                       "evaluator_role", "role"})



def _module_for_command(command: str) -> str | None:
    """The importable module a node command runs, if any.

    Commands are ``python -m <module> [args]``. The science runner's adapter is
    ``python -m srf.ops.run <module>:<function>``, so when the target is the
    adapter the real module is its first argument. Anything else (a shell
    builtin, a wrapper script) has no module and therefore no declared knobs.
    """
    import re

    match = re.search(r"-m\s+([\w.]+)", command)
    if not match:
        return None
    module = match.group(1)
    if not module.endswith(".run"):
        return module
    rest = command[match.end():].strip()
    target = rest.split()[0] if rest else ""
    return target.split(":", 1)[0] if ":" in target else (target or None)


def op_knobs(command: str) -> dict[str, dict[str, Any]]:
    """The knobs an op declares it reads, read from the op itself.

    An op-level knob is consumed by the op through ``SRF_KNOBS`` rather than
    through a node field, so it cannot be derived from the graph alone. Without
    this it survives only because someone hand-wrote it into ``knob_specs``,
    which is the hole this closes: the mode's surface then comes from the graph
    and from the code its nodes run, and from nothing else.

    An op that cannot be imported declares nothing. That is deliberate: probing
    must never make a run fail, and a node whose module is not importable here
    (a different interpreter, a missing dependency) simply contributes no knobs.
    """
    module_name = _module_for_command(command)
    if not module_name:
        return {}
    try:
        module = importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 - a node's op may not be importable here
        return {}
    declared = getattr(module, "KNOBS", None)
    if not isinstance(declared, dict):
        return {}
    return {str(k): v for k, v in declared.items() if isinstance(v, dict)}


@dataclass
class ModeParameters:
    """A mode's optimizable surface, and what it declares but cannot honour."""

    knobs: list[OptKnob] = field(default_factory=list)
    dead: list[dict[str, str]] = field(default_factory=list)
    unverified: list[dict[str, str]] = field(default_factory=list)

    def names(self) -> list[str]:
        return [k.name for k in self.knobs]

    def report(self) -> list[str]:
        """Human-readable problems with this mode's declared surface."""
        out = [
            f"'{d['name']}' on node '{d['node_id']}' is declared but not consumed "
            f"({d['description']}) — the optimizer would move it and nothing would change"
            for d in self.dead
        ]
        out += [
            f"'{u['name']}' on node '{u['node_id']}' has unverified consumption; it is "
            f"not advertised until its consumer is confirmed"
            for u in self.unverified
        ]
        return out


def mode_parameters(workflow: Workflow) -> ModeParameters:
    """Derive every optimizable knob from the mode's own nodes.

    Node-field knobs are named ``<node>.<field>`` so a proposal reads as a
    parameter path, and graph-level knobs already declared in ``knob_specs`` are
    carried through unchanged — those are consumed by an op through ``SRF_KNOBS``
    rather than by a node field.
    """
    from factory.workflow.package import OptKnob

    params = ModeParameters()

    for node_id, node in workflow.nodes.items():
        for name, spec in FIELD_SPECS.items():
            if name not in type(node).model_fields:
                continue
            value = getattr(node, name, None)
            if value is None:
                continue
            entry = {"name": f"{node_id}.{name}", "node_id": node_id,
                     "description": spec.description}
            if spec.status == "dead":
                params.dead.append(entry)
                continue
            if spec.status == "unverified":
                params.unverified.append(entry)
                continue
            params.knobs.append(
                OptKnob(
                    name=f"{node_id}.{name}",
                    kind=spec.kind,  # type: ignore[arg-type]
                    node_id=node_id,
                    default=value if isinstance(value, (str, float, int)) else "",
                    bounds=list(spec.bounds),
                    expandable=spec.expandable,
                    expansion_hint=spec.consumed_by,
                    description=f"{spec.description} (read by {spec.consumed_by})",
                )
            )

    # Knobs an op declares it reads, resolved from the module each node runs.
    for node_id, node in workflow.nodes.items():
        command = getattr(node, "command", "") or ""
        if not command:
            continue
        for name, spec in op_knobs(command).items():
            bounds = list(spec.get("bounds", []) or [])
            params.knobs.append(
                OptKnob(
                    name=name,
                    kind=str(spec.get("kind", "threshold")),  # type: ignore[arg-type]
                    node_id=node_id,
                    default=spec.get("default", ""),
                    bounds=bounds,
                    expandable=bool(spec.get("expandable", False)),
                    expansion_hint=str(spec.get("expansion_hint", "")),
                    description=str(spec.get("description", "")),
                )
            )

    # Graph-level knobs, still honoured so an existing mode keeps working.
    for name, spec in (workflow.knob_specs or {}).items():
        params.knobs.append(
            OptKnob(
                name=name,
                kind=str(spec.get("kind", "threshold")),  # type: ignore[arg-type]
                node_id=str(spec.get("node_id", "")),
                default=spec.get("default", ""),
                bounds=list(spec.get("bounds", []) or []),
                expandable=bool(spec.get("expandable", False))
                or name in (workflow.knob_expandable or {}),
                expansion_hint=str(spec.get("expansion_hint", "")),
                description=str(spec.get("description", "")),
            )
        )
    seen: set[str] = set()
    unique: list[OptKnob] = []
    for knob in params.knobs:
        if knob.name in seen:
            continue
        seen.add(knob.name)
        unique.append(knob)
    params.knobs = unique
    return params
