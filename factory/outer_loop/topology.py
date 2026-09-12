"""Topology knobs: named structural edits as a bounded, proposable domain.

``OptKnob.kind`` has declared ``"topology"`` since the type was introduced, but
nothing consumed it: a topology knob was accepted by the schema and ignored by
every optimizer. This module makes it real.

A topology knob names one node and lists, in ``bounds``, the structural
operations that may be applied to it. Applying a proposal means looking the
operation up in :class:`TopologyRegistry` and running it, so a structural change
is chosen, validated and recorded exactly like a threshold change:

- the choice lives in ``knob_values``, so it survives compile round-trips and
  joins the MAP-Elites feature vector rather than being an invisible side effect;
- an unknown operation is rejected, not guessed at;
- every operation returns a :class:`MutationRecord`, so genealogy keeps working.

An operation that needs more than a scalar to describe it — inserting a specific
node, redirecting an edge to a specific target — is deliberately absent. A knob
value cannot express those, and inventing a default target would record a
rationale that does not describe what happened. They remain available to the
structural mutation operators, which can carry the extra argument.
"""

from __future__ import annotations

from typing import Callable

import structlog

from factory.outer_loop.mutations import (
    MutationRecord,
    mutate_params,
    mutate_prompt,
    parallelize,
    remove_node,
    serialize,
)
from factory.workflow.primitives import Workflow

log = structlog.get_logger()

TopologyOp = Callable[[Workflow, str, set[str]], "tuple[Workflow, MutationRecord] | None"]


def _remove(workflow: Workflow, node_id: str, frozen: set[str]):
    return remove_node(workflow, node_id, frozen_nodes=frozen)


def _serialize(workflow: Workflow, node_id: str, frozen: set[str]):
    """Collapse a fork/join pair that starts at this node back into a chain."""
    return serialize(workflow, node_id, frozen_nodes=frozen)


def _parallelize_next(workflow: Workflow, node_id: str, frozen: set[str]):
    """Run this node and its immediate successor concurrently."""
    successors = [e.target for e in workflow.edges if e.source == node_id]
    if not successors:
        return None
    return parallelize(workflow, [node_id, successors[0]], frozen_nodes=frozen)


def _prompt(workflow: Workflow, node_id: str, frozen: set[str]):
    """Rewrite this node's prompt without a rewriter (append a variant)."""
    return mutate_prompt(workflow, node_id, frozen_nodes=frozen, rewriter=None)


#: Numeric node fields a topology knob may nudge, in preference order. Only the
#: first present field is changed, so one proposal makes one auditable change.
_NUMERIC_FIELDS = ("max_iterations", "timeout", "temperature")


def _params(workflow: Workflow, node_id: str, frozen: set[str]):
    """Nudge one numeric parameter on this node.

    An empty change set is a no-op, so the field and its direction are chosen
    here: doubling a bound or raising a sampling temperature are the two edits a
    topology knob can express without inventing a value the node does not accept.
    """
    node = workflow.nodes.get(node_id)
    if node is None:
        return None
    for field_name in _NUMERIC_FIELDS:
        current = getattr(node, field_name, None)
        if isinstance(current, (int, float)):
            updated = current * 2 if field_name == "max_iterations" else current + 0.1
            if field_name == "temperature":
                updated = min(1.0, round(updated, 2))
            else:
                updated = int(updated)
            return mutate_params(workflow, node_id, {field_name: updated}, frozen_nodes=frozen)
    return None


class TopologyRegistry:
    """Name-addressed structural operations a topology knob may select."""

    _ops: dict[str, TopologyOp] = {}

    @classmethod
    def register(cls, name: str, op: TopologyOp) -> None:
        cls._ops[name] = op

    @classmethod
    def get(cls, name: str) -> TopologyOp | None:
        return cls._ops.get(name)

    @classmethod
    def names(cls) -> list[str]:
        return sorted(cls._ops)

    @classmethod
    def reset(cls) -> None:
        """Drop every registration. For tests."""
        cls._ops.clear()


TopologyRegistry.register("remove", _remove)
TopologyRegistry.register("serialize", _serialize)
TopologyRegistry.register("parallelize_next", _parallelize_next)
TopologyRegistry.register("prompt", _prompt)
TopologyRegistry.register("params", _params)


def apply_topology(
    workflow: Workflow,
    node_id: str,
    operation: str,
    frozen: set[str] | None = None,
) -> tuple[Workflow, MutationRecord] | None:
    """Apply a named structural operation to one node.

    Returns ``None`` when the operation is unknown or inapplicable, which the
    caller treats as a rejected proposal: a topology knob has a closed domain and
    an unlisted operation is a mistake to report, not to repair.
    """
    op = TopologyRegistry.get(operation)
    if op is None:
        log.warning(
            "topology_unknown_operation",
            operation=operation,
            known=TopologyRegistry.names(),
        )
        return None
    result = op(workflow, node_id, set(frozen or set()))
    if result is None:
        log.info("topology_operation_inapplicable", operation=operation, node_id=node_id)
    return result
