"""Tests for factory.optimization.types — construction and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from factory.models import AggregateMethod
from factory.optimization.types import (
    ExecutionResult,
    GateResult,
    GraphMutation,
    LoopConfig,
    Patch,
    SlotEdit,
    StepRecord,
)


class TestSlotEdit:
    def test_construction(self) -> None:
        edit = SlotEdit(slot_name="prompt", old_value="old", new_value="new")
        assert edit.slot_name == "prompt"
        assert edit.old_value == "old"
        assert edit.new_value == "new"


class TestGraphMutation:
    def test_update_node(self) -> None:
        m = GraphMutation(op="update_node", node_id="n1", field="prompt", value="new")
        assert m.op == "update_node"
        assert m.node_id == "n1"

    def test_remove_node(self) -> None:
        m = GraphMutation(op="remove_node", node_id="n2")
        assert m.op == "remove_node"

    def test_add_edge(self) -> None:
        m = GraphMutation(op="add_edge", source="a", target="b")
        assert m.source == "a"
        assert m.target == "b"

    def test_remove_edge(self) -> None:
        m = GraphMutation(op="remove_edge", source="a", target="b")
        assert m.op == "remove_edge"

    def test_invalid_op_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GraphMutation(op="invalid_op")  # type: ignore[arg-type]

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            GraphMutation(op="update_node", node_id="n1", bogus="x")  # type: ignore[call-arg]


class TestPatch:
    def test_empty_patch(self) -> None:
        p = Patch()
        assert p.prompt_edits == []
        assert p.graph_mutations == []
        assert p.reasoning == ""

    def test_with_edits(self) -> None:
        edit = SlotEdit("s", "a", "b")
        mut = GraphMutation(op="remove_node", node_id="n1")
        p = Patch(prompt_edits=[edit], graph_mutations=[mut], reasoning="test")
        assert len(p.prompt_edits) == 1
        assert len(p.graph_mutations) == 1
        assert p.reasoning == "test"


class TestExecutionResult:
    def test_defaults(self) -> None:
        r = ExecutionResult(returncode=0)
        assert r.returncode == 0
        assert r.artifacts == []
        assert r.duration_s == 0.0
        assert r.cost_usd == 0.0

    def test_with_values(self) -> None:
        r = ExecutionResult(returncode=1, artifacts=["a.json"], duration_s=5.5, cost_usd=0.1)
        assert r.returncode == 1
        assert r.artifacts == ["a.json"]


class TestGateResult:
    def test_accepted(self) -> None:
        g = GateResult(accepted=True, reason="better", candidate_score=0.8, current_score=0.7, best_score=0.8)
        assert g.accepted is True

    def test_rejected(self) -> None:
        g = GateResult(accepted=False, reason="worse", candidate_score=0.5, current_score=0.7, best_score=0.7)
        assert g.accepted is False


class TestStepRecord:
    def test_defaults(self) -> None:
        r = StepRecord(step_number=1)
        assert r.step_number == 1
        assert r.score_start is None
        assert r.verdict is None
        assert r.artifacts == []
        assert r.patch is None

    def test_with_values(self) -> None:
        r = StepRecord(
            step_number=3,
            score_start=0.5,
            score_end=0.7,
            score_delta=0.2,
            duration_s=10.0,
            cost_usd=0.5,
            verdict="keep",
            artifacts=["eval.json"],
        )
        assert r.score_delta == 0.2
        assert r.verdict == "keep"


class TestLoopConfig:
    def test_defaults(self) -> None:
        c = LoopConfig()
        assert c.epochs == 1
        assert c.steps_per_epoch == 1
        assert c.plateau_threshold == 3
        assert c.aggregate == AggregateMethod.mean
        assert c.inner_surfaces == []
        assert c.outer_surfaces == []
        assert c.frozen_nodes == frozenset()
        assert c.max_inner_runs_per_cycle is None

    def test_custom_values(self) -> None:
        c = LoopConfig(
            epochs=5,
            steps_per_epoch=10,
            plateau_threshold=5,
            aggregate=AggregateMethod.median,
            inner_surfaces=["a.md"],
            outer_surfaces=["b.py"],
            frozen_nodes=frozenset({"n1"}),
            max_inner_runs_per_cycle=3,
        )
        assert c.epochs == 5
        assert c.frozen_nodes == frozenset({"n1"})

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            LoopConfig(bad_field="x")  # type: ignore[call-arg]
