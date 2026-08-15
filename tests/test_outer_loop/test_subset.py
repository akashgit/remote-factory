"""Tests for SubsetSelector and FixedSubsetSelector."""

from __future__ import annotations

from factory.outer_loop.subset import FixedSubsetSelector, SubsetSelector


class TestFixedSubsetSelector:
    def test_returns_configured_instances(self) -> None:
        selector = FixedSubsetSelector(["t1", "t2", "t3"])
        result = selector.select(["t1", "t2", "t3", "t4", "t5"], generation=0, budget_remaining=100)
        assert result == ["t1", "t2", "t3"]

    def test_ignores_generation_and_budget(self) -> None:
        selector = FixedSubsetSelector(["a", "b"])
        r1 = selector.select(["a", "b", "c"], generation=0, budget_remaining=100)
        r2 = selector.select(["a", "b", "c"], generation=5, budget_remaining=10)
        assert r1 == r2

    def test_returns_copy(self) -> None:
        instances = ["x", "y"]
        selector = FixedSubsetSelector(instances)
        result = selector.select([], generation=0, budget_remaining=50)
        result.append("z")
        assert selector.select([], generation=0, budget_remaining=50) == ["x", "y"]

    def test_protocol_conformance(self) -> None:
        selector = FixedSubsetSelector(["t1"])
        assert isinstance(selector, SubsetSelector)

    def test_empty_instances(self) -> None:
        selector = FixedSubsetSelector([])
        assert selector.select(["a", "b"], generation=0, budget_remaining=10) == []
