"""Pluggable optimizers for the outer loop.

The outer loop is an optimizer loop. The *gradient* is what an inner run
produces — its scores, verdicts, errors and traces, assembled into a
``CycleRecord`` and the per-candidate trace facts. An *optimizer* turns that
gradient into the next candidates, exactly as Adam, SGD and Muon each turn the
same gradient into a different step.

Keeping the two apart is what makes the search strategy replaceable:

- ``RandomOptimizer`` ignores the gradient beyond parent selection and applies
  weighted random structural mutations. It is the baseline, and it is what the
  engine did before this seam existed.
- A reasoning optimizer (see :class:`Optimizer` implementations added later)
  reads the trace facts and proposes targeted edits, expressed as values for the
  declared ``OptKnob`` surface.

Where the step lands is not the optimizer's choice: every proposal is a
:class:`~factory.workflow.primitives.Workflow`, and the engine still owns parent
genealogy, the novelty filter, the archive and the budget.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

from factory.outer_loop.mutations import (
    MutationStrategy,
    WeightedRandomStrategy,
    apply_random_mutation,
)
from factory.outer_loop.reflector import ReflectionReport
from factory.workflow.primitives import Workflow

#: Samples one parent to mutate, returning its id and workflow, or None when the
#: archive is empty. Supplied by the engine so selection policy stays one place.
ParentSampler = Callable[[], "tuple[str, Workflow] | None"]


@dataclass
class ProposalContext:
    """Everything an optimizer is allowed to condition its proposal on."""

    generation: int
    sample_parent: ParentSampler
    frozen_nodes: set[str] = field(default_factory=set)
    reflection: ReflectionReport | None = None
    archive_stats: dict[str, object] = field(default_factory=dict)
    #: Per-candidate trace facts (see refactory_dsh.trace), newest last. Empty
    #: for an optimizer that does not consume the gradient.
    traces: list[dict[str, object]] = field(default_factory=list)
    #: The declared OptKnob surface the optimizer may move, as plain dicts so an
    #: optimizer never has to import the workflow package to read it.
    knob_surface: list[dict[str, object]] = field(default_factory=list)


@dataclass
class Proposal:
    """One candidate an optimizer wants the engine to consider."""

    workflow: Workflow
    operator: str
    rationale: str = ""
    target_node: str | None = None
    before: dict[str, object] = field(default_factory=dict)
    after: dict[str, object] = field(default_factory=dict)
    parent_id: str | None = None
    #: Which optimizer produced this, for the operator leaderboard.
    optimizer: str = ""


@runtime_checkable
class ModelClient(Protocol):
    """A one-shot text completion.

    The optimizer needs exactly this and nothing more from a model. Keeping it
    this narrow lets the client be a direct API call, a headless agent run, or a
    file bridge answered by a GUI-visible agent node, with no change to the
    optimizer.
    """

    def complete(self, prompt: str, *, system: str = "") -> str:
        """Return the model's text for ``prompt``. Raises on transport failure."""
        ...


@runtime_checkable
class TraceSource(Protocol):
    """Access to per-candidate trace facts.

    The optimizer reads the gradient through this rather than a file layout, so
    the same optimizer works against any loop that can describe what its runs
    produced.
    """

    def recent(self, limit: int) -> list[dict[str, object]]:
        """Return up to ``limit`` most recent trace facts, newest last."""
        ...


@runtime_checkable
class Optimizer(Protocol):
    """Turns the gradient into candidate workflows."""

    name: str

    def propose(self, ctx: ProposalContext, count: int) -> list[Proposal]:
        """Return up to ``count`` candidates. May return fewer, and may
        over-propose: the engine applies the novelty filter and the budget."""
        ...


class RandomOptimizer:
    """Baseline optimizer: weighted random structural mutation.

    Consumes no gradient beyond which parent the sampler hands back. Kept as the
    reference so a reasoning optimizer can be compared against it on the same
    archive, budget and trace surface.
    """

    name = "random"

    def __init__(
        self,
        strategy: MutationStrategy | None = None,
        max_attempts: int = 10,
    ) -> None:
        self._strategy = strategy or WeightedRandomStrategy()
        self._max_attempts = max_attempts

    @property
    def strategy(self) -> MutationStrategy:
        return self._strategy

    def propose(self, ctx: ProposalContext, count: int) -> list[Proposal]:
        proposals: list[Proposal] = []
        for _ in range(count):
            sampled = ctx.sample_parent()
            if sampled is None:
                continue
            parent_id, parent_wf = sampled
            for _attempt in range(self._max_attempts):
                result = apply_random_mutation(
                    parent_wf,
                    self._strategy,
                    ctx.generation,
                    frozen_nodes=ctx.frozen_nodes,
                    reflection_report=ctx.reflection,
                )
                if result is None:
                    continue
                child_wf, record = result
                proposals.append(
                    Proposal(
                        workflow=child_wf,
                        operator=record.operator.value,
                        rationale=record.rationale or "",
                        target_node=record.target_node,
                        before=dict(record.before or {}),
                        after=dict(record.after or {}),
                        parent_id=parent_id,
                        optimizer=self.name,
                    )
                )
                break
        return proposals


class OptimizerRegistry:
    """Name-addressed registry of optimizers.

    Mirrors the mode registry: an optimizer is chosen by name so the GUI and the
    CLI can list what is available and the user can switch without editing code.

    Factories are registered rather than instances because an optimizer may need
    per-run construction (a model client, a strategy bound to one engine), and a
    shared instance would leak that state across runs.
    """

    _factories: dict[str, Callable[..., Optimizer]] = {}

    @classmethod
    def register(cls, name: str, factory: Callable[..., Optimizer]) -> None:
        cls._factories[name] = factory

    @classmethod
    def create(cls, name: str, **kwargs: object) -> Optimizer | None:
        factory = cls._factories.get(name)
        return factory(**kwargs) if factory is not None else None

    @classmethod
    def names(cls) -> list[str]:
        return sorted(cls._factories)

    @classmethod
    def reset(cls) -> None:
        """Drop every registration. For tests."""
        cls._factories.clear()


OptimizerRegistry.register(RandomOptimizer.name, RandomOptimizer)
