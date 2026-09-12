"""Tests for the pluggable outer-loop optimizer seam."""

from __future__ import annotations

import random

from factory.outer_loop.engine import SwarmEngine
from factory.outer_loop.models import SwarmConfig
from factory.outer_loop.optimizers import (
    OptimizerRegistry,
    Proposal,
    ProposalContext,
    RandomOptimizer,
)
from factory.workflow.primitives import Workflow


def _workflow(nodes: int = 4) -> Workflow:
    """A minimal valid workflow, built through the same path the engine uses."""
    return Workflow.from_dict(
        {
            "name": "t",
            "nodes": {
                f"n{i}": {"id": f"n{i}", "_type": "FnNode", "command": "true"} for i in range(nodes)
            },
            "edges": [
                {"source": f"n{i}", "target": f"n{i + 1}", "condition": None}
                for i in range(nodes - 1)
            ],
            "start_node": "n0",
            "terminal": True,
            "knob_values": {"k": 1},
            "knob_bounds": {"k": [1, 2]},
            "knob_specs": {"k": {"kind": "threshold", "node_id": "n0", "default": 1}},
        }
    )


def test_registry_lists_random_and_creates_instances() -> None:
    assert "random" in OptimizerRegistry.names()
    a = OptimizerRegistry.create("random")
    b = OptimizerRegistry.create("random")
    assert isinstance(a, RandomOptimizer) and isinstance(b, RandomOptimizer)
    assert a is not b  # factories, not a shared instance
    assert OptimizerRegistry.create("does-not-exist") is None


def test_random_optimizer_proposes_from_sampled_parent() -> None:
    random.seed(0)
    wf = _workflow()
    ctx = ProposalContext(generation=1, sample_parent=lambda: ("p1", wf))
    proposals = RandomOptimizer().propose(ctx, 3)
    assert proposals, "expected at least one proposal"
    for p in proposals:
        assert isinstance(p, Proposal)
        assert p.optimizer == "random"
        assert p.operator
        assert p.parent_id == "p1"
        assert isinstance(p.workflow, Workflow)


def test_random_optimizer_returns_nothing_without_a_parent() -> None:
    ctx = ProposalContext(generation=0, sample_parent=lambda: None)
    assert RandomOptimizer().propose(ctx, 3) == []


def test_context_exposes_the_knob_surface() -> None:
    captured: dict[str, object] = {}

    class Capturing:
        name = "capturing"

        def propose(self, ctx: ProposalContext, count: int) -> list[Proposal]:
            captured["surface"] = ctx.knob_surface
            captured["traces"] = ctx.traces
            return []

    config = SwarmConfig(
        benchmark="srf",
        budget=2,
        population_size=2,
        tournament_size=1,
        training_instances=["t"],
        designer_count=0,
    )
    engine = SwarmEngine(config=config, evaluator=None, optimizer=Capturing())  # type: ignore[arg-type]
    population = engine.seed(_workflow(), config)
    # The surface is read from the archive, which evolve_generation fills before
    # the optimizer is consulted; mirror that here.
    for individual in population.individuals:
        engine.archive.add(individual)
    surface = engine._knob_surface()
    assert [s["name"] for s in surface] == ["k"]
    assert surface[0]["kind"] == "threshold"
    assert surface[0]["expandable"] is False


def test_engine_defaults_to_the_random_optimizer() -> None:
    config = SwarmConfig(
        benchmark="srf",
        budget=1,
        population_size=2,
        tournament_size=1,
        training_instances=["t"],
    )
    engine = SwarmEngine(config=config, evaluator=None)  # type: ignore[arg-type]
    assert isinstance(engine.optimizer, RandomOptimizer)
