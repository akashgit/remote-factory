"""Tests for Population and MAPElitesArchive."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.outer_loop.models import Individual
from factory.outer_loop.population import MAPElitesArchive, Population
from factory.workflow.primitives import Workflow


class TestPopulation:
    def test_add_and_size(self) -> None:
        pop = Population()
        assert pop.size == 0
        ind = Individual(id="a", workflow_data={"name": "w"}, score=0.5, features=(1, 0, 2, 1))
        pop.add(ind)
        assert pop.size == 1

    def test_remove(self) -> None:
        pop = Population()
        ind = Individual(id="a", workflow_data={"name": "w"}, score=0.5, features=(1, 0, 2, 1))
        pop.add(ind)
        removed = pop.remove("a")
        assert removed is not None
        assert pop.size == 0
        assert pop.remove("nonexistent") is None

    def test_get(self) -> None:
        pop = Population()
        ind = Individual(id="a", workflow_data={"name": "w"}, score=0.5, features=(1, 0, 2, 1))
        pop.add(ind)
        assert pop.get("a") is not None
        assert pop.get("b") is None

    def test_best(self) -> None:
        pop = Population()
        assert pop.best() is None
        pop.add(Individual(id="a", workflow_data={}, score=0.5, features=(1, 0, 2, 1)))
        pop.add(Individual(id="b", workflow_data={}, score=0.9, features=(2, 1, 3, 2)))
        pop.add(Individual(id="c", workflow_data={}, score=0.7, features=(1, 1, 2, 1)))
        best = pop.best()
        assert best is not None
        assert best.id == "b"

    def test_mean_score(self) -> None:
        pop = Population()
        assert pop.mean_score() == 0.0
        pop.add(Individual(id="a", workflow_data={}, score=0.4, features=()))
        pop.add(Individual(id="b", workflow_data={}, score=0.8, features=()))
        assert pop.mean_score() == pytest.approx(0.6)

    def test_individuals_list(self) -> None:
        pop = Population()
        pop.add(Individual(id="a", workflow_data={}, score=0.5, features=()))
        pop.add(Individual(id="b", workflow_data={}, score=0.7, features=()))
        assert len(pop.individuals) == 2

    def test_make_individual(self, simple_workflow: Workflow) -> None:
        ind = Population.make_individual(simple_workflow, generation=1, score=0.8)
        assert ind.generation == 1
        assert ind.score == 0.8
        assert len(ind.features) == 8
        assert ind.parent_id is None

    def test_serialization_round_trip(self, simple_workflow: Workflow, tmp_path: Path) -> None:
        pop = Population()
        ind = Population.make_individual(simple_workflow, generation=0, score=0.7)
        pop.add(ind)

        pop.save(tmp_path / "pop")
        loaded = Population.load(tmp_path / "pop")

        assert loaded.size == 1
        loaded_ind = loaded.individuals[0]
        assert loaded_ind.id == ind.id
        assert loaded_ind.score == ind.score


class TestMAPElitesArchive:
    def test_add_and_size(self) -> None:
        archive = MAPElitesArchive()
        assert archive.size == 0
        ind = Individual(id="a", workflow_data={}, score=0.5, features=(1, 0, 2, 1))
        assert archive.add(ind) is True
        assert archive.size == 1

    def test_add_replaces_lower_score(self) -> None:
        archive = MAPElitesArchive()
        ind1 = Individual(id="a", workflow_data={}, score=0.5, features=(1, 0, 2, 1))
        ind2 = Individual(id="b", workflow_data={}, score=0.9, features=(1, 0, 2, 1))
        archive.add(ind1)
        assert archive.add(ind2) is True
        assert archive.size == 1
        assert archive.best().id == "b"  # type: ignore[union-attr]

    def test_add_keeps_higher_score(self) -> None:
        archive = MAPElitesArchive()
        ind1 = Individual(id="a", workflow_data={}, score=0.9, features=(1, 0, 2, 1))
        ind2 = Individual(id="b", workflow_data={}, score=0.5, features=(1, 0, 2, 1))
        archive.add(ind1)
        assert archive.add(ind2) is False
        assert archive.best().id == "a"  # type: ignore[union-attr]

    def test_best_empty(self) -> None:
        assert MAPElitesArchive().best() is None

    def test_best(self) -> None:
        archive = MAPElitesArchive()
        archive.add(Individual(id="a", workflow_data={}, score=0.5, features=(1, 0, 2, 1)))
        archive.add(Individual(id="b", workflow_data={}, score=0.9, features=(2, 1, 3, 2)))
        best = archive.best()
        assert best is not None
        assert best.id == "b"

    def test_sample_parent_returns_something(self) -> None:
        archive = MAPElitesArchive()
        assert archive.sample_parent() is None
        archive.add(Individual(id="a", workflow_data={}, score=0.5, features=(1, 0, 2, 1)))
        result = archive.sample_parent(tournament_size=1)
        assert result is not None
        assert result.id == "a"

    def test_tournament_selection(self) -> None:
        archive = MAPElitesArchive()
        for i in range(10):
            archive.add(
                Individual(id=f"i{i}", workflow_data={}, score=i * 0.1, features=(i, 0, i, 0))
            )
        results = [archive.sample_parent(tournament_size=3) for _ in range(20)]
        scores = [r.score for r in results if r is not None]
        assert all(s >= 0.0 for s in scores)

    def test_pareto_front_single(self) -> None:
        archive = MAPElitesArchive()
        archive.add(Individual(id="a", workflow_data={}, score=0.5, features=(1, 0, 2, 1)))
        front = archive.pareto_front()
        assert len(front) == 1

    def test_pareto_front_dominated(self) -> None:
        archive = MAPElitesArchive()
        archive.add(Individual(id="a", workflow_data={}, score=0.5, features=(1, 0, 2, 1)))
        archive.add(Individual(id="b", workflow_data={}, score=0.9, features=(2, 1, 3, 2)))
        front = archive.pareto_front()
        assert len(front) == 1
        assert front[0].id == "b"

    def test_pareto_front_non_dominated(self) -> None:
        archive = MAPElitesArchive()
        archive.add(Individual(id="a", workflow_data={}, score=0.9, features=(1, 0, 5, 0)))
        archive.add(Individual(id="b", workflow_data={}, score=0.5, features=(5, 3, 1, 3)))
        front = archive.pareto_front()
        assert len(front) == 2

    def test_diversity_metric_empty(self) -> None:
        assert MAPElitesArchive().diversity_metric() == 0.0

    def test_diversity_metric_nonzero(self) -> None:
        archive = MAPElitesArchive()
        archive.add(Individual(id="a", workflow_data={}, score=0.5, features=(1, 0, 2, 1)))
        archive.add(Individual(id="b", workflow_data={}, score=0.7, features=(2, 1, 3, 2)))
        d = archive.diversity_metric()
        assert 0.0 < d <= 1.0

    def test_serialization_round_trip(self, tmp_path: Path) -> None:
        archive = MAPElitesArchive()
        archive.add(Individual(id="a", workflow_data={}, score=0.5, features=(1, 0, 2, 1)))
        archive.add(Individual(id="b", workflow_data={}, score=0.9, features=(2, 1, 3, 2)))

        archive.save(tmp_path / "archive")
        loaded = MAPElitesArchive.load(tmp_path / "archive")

        assert loaded.size == 2
        assert loaded.best() is not None
        assert loaded.best().id == "b"  # type: ignore[union-attr]


class TestRankWeightedSelection:
    def test_rank_weighted_biases_toward_best(self) -> None:
        archive = MAPElitesArchive()
        archive.add(Individual(id="bad", workflow_data={}, score=-100.0, features=(0, 0, 1, 0)))
        archive.add(Individual(id="ok", workflow_data={}, score=0.0, features=(1, 0, 1, 0)))
        archive.add(Individual(id="good", workflow_data={}, score=100.0, features=(2, 0, 1, 0)))
        counts: dict[str, int] = {"bad": 0, "ok": 0, "good": 0}
        for _ in range(300):
            p = archive.sample_parent(tournament_size=1, rank_weighted=True)
            assert p is not None
            counts[p.id] += 1
        # With rank weighting (weights 1,2,3), "good" should be picked ~50% of the time
        assert counts["good"] > counts["bad"]
        assert counts["good"] > 100  # at least ~33%

    def test_rank_weighted_false_is_uniform(self) -> None:
        archive = MAPElitesArchive()
        archive.add(Individual(id="a", workflow_data={}, score=-1000.0, features=(0, 0, 1, 0)))
        archive.add(Individual(id="b", workflow_data={}, score=1000.0, features=(1, 0, 1, 0)))
        counts: dict[str, int] = {"a": 0, "b": 0}
        for _ in range(200):
            p = archive.sample_parent(tournament_size=1, rank_weighted=False)
            assert p is not None
            counts[p.id] += 1
        # Uniform: both should be roughly 50/50
        assert counts["a"] > 50
        assert counts["b"] > 50
