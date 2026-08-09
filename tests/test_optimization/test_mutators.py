"""Tests for factory.optimization.mutators — protocol conformance and behavior."""

from __future__ import annotations

from factory.optimization.mutators import (
    OverwriteMutator,
    SkillOptMutator,
    UnifiedMutator,
)
from factory.optimization.protocols import Mutator
from factory.optimization.surface import Surface
from factory.optimization.types import ExecutionResult, GraphMutation


class TestMutatorProtocolConformance:
    def test_skillopt_mutator(self) -> None:
        assert isinstance(SkillOptMutator(), Mutator)

    def test_overwrite_mutator(self) -> None:
        assert isinstance(OverwriteMutator(), Mutator)

    def test_unified_mutator(self) -> None:
        assert isinstance(UnifiedMutator(), Mutator)


class TestSkillOptMutator:
    def test_returns_empty_patch(self) -> None:
        m = SkillOptMutator()
        surface = Surface(prompt_slots={"p1": "v1"})
        result = m.propose(surface, ExecutionResult(returncode=0), [])
        assert result.prompt_edits == []
        assert "stub" in result.reasoning.lower()


class TestOverwriteMutator:
    def test_no_overwrite_text(self) -> None:
        m = OverwriteMutator()
        result = m.propose(Surface(), ExecutionResult(returncode=0), [])
        assert result.graph_mutations == []

    def test_mutations_from_dicts(self) -> None:
        raw = [
            {"op": "update_node", "node_id": "n1", "field": "prompt", "value": "new"},
            {"op": "remove_node", "node_id": "n2"},
        ]
        mutations = OverwriteMutator.mutations_from_dicts(raw)
        assert len(mutations) == 2
        assert isinstance(mutations[0], GraphMutation)
        assert mutations[0].op == "update_node"
        assert mutations[1].op == "remove_node"


class TestUnifiedMutator:
    def test_composes_both(self) -> None:
        m = UnifiedMutator()
        result = m.propose(Surface(), ExecutionResult(returncode=0), [])
        assert isinstance(result.prompt_edits, list)
        assert isinstance(result.graph_mutations, list)
