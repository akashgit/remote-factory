"""Population management and MAP-Elites archive for evolutionary search."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from factory.outer_loop.models import Individual
from factory.outer_loop.similarity import compute_features

if TYPE_CHECKING:
    from factory.workflow.primitives import Workflow

log = structlog.get_logger()


class Population:
    """Manages a collection of Individual candidates."""

    def __init__(self) -> None:
        self._individuals: dict[str, Individual] = {}

    @property
    def size(self) -> int:
        return len(self._individuals)

    @property
    def individuals(self) -> list[Individual]:
        return list(self._individuals.values())

    def add(self, individual: Individual) -> None:
        self._individuals[individual.id] = individual

    def remove(self, individual_id: str) -> Individual | None:
        return self._individuals.pop(individual_id, None)

    def get(self, individual_id: str) -> Individual | None:
        return self._individuals.get(individual_id)

    def best(self) -> Individual | None:
        if not self._individuals:
            return None
        return max(self._individuals.values(), key=lambda i: i.score)

    def mean_score(self) -> float:
        if not self._individuals:
            return 0.0
        return sum(i.score for i in self._individuals.values()) / len(self._individuals)

    @staticmethod
    def make_individual(
        workflow: Workflow,
        *,
        generation: int = 0,
        parent_id: str | None = None,
        mutation_record: object = None,
        score: float = 0.0,
        cost_usd: float = 0.0,
    ) -> Individual:
        """Create an Individual from a Workflow, computing features automatically."""
        from factory.outer_loop.models import MutationRecord

        features = compute_features(workflow)
        return Individual(
            id=uuid.uuid4().hex[:12],
            workflow_data=workflow.to_dict(),
            score=score,
            features=features,
            generation=generation,
            parent_id=parent_id,
            mutation_record=mutation_record if isinstance(mutation_record, MutationRecord) else None,
            cost_usd=cost_usd,
        )

    def save(self, directory: Path) -> None:
        """Serialize the population to a directory."""
        directory.mkdir(parents=True, exist_ok=True)
        data = [ind.model_dump(mode="json") for ind in self._individuals.values()]
        (directory / "population.json").write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, directory: Path) -> Population:
        """Deserialize a population from a directory."""
        pop = cls()
        path = directory / "population.json"
        if path.exists():
            data = json.loads(path.read_text())
            for item in data:
                pop.add(Individual.model_validate(item))
        return pop


class MAPElitesArchive:
    """4D fixed-resolution grid archive for quality-diversity search.

    Axes: (depth, fork_degree, agent_count, gate_count).
    Each cell stores the best-scoring Individual for that feature combination.
    """

    def __init__(self) -> None:
        self._grid: dict[tuple[int, ...], Individual] = {}

    @property
    def size(self) -> int:
        return len(self._grid)

    def add(self, individual: Individual) -> bool:
        """Add an individual to the archive. Returns True if it was inserted or replaced."""
        key = individual.features
        existing = self._grid.get(key)
        if existing is None or individual.score > existing.score:
            self._grid[key] = individual
            return True
        return False

    def best(self) -> Individual | None:
        if not self._grid:
            return None
        return max(self._grid.values(), key=lambda i: i.score)

    def all_individuals(self) -> list[Individual]:
        return list(self._grid.values())

    def sample_parent(
        self,
        tournament_size: int = 3,
        rank_weighted: bool = False,
    ) -> Individual | None:
        """Tournament selection: pick tournament_size individuals, return the best.

        When ``rank_weighted=True``, individuals are drawn with probability
        proportional to their rank (best=N, worst=1) instead of uniformly.
        This biases toward stronger parents while still allowing weaker
        individuals a small chance, preserving diversity.
        """
        import random

        individuals = list(self._grid.values())
        if not individuals:
            return None
        k = min(tournament_size, len(individuals))
        if rank_weighted and len(individuals) >= 2:
            ranked = sorted(individuals, key=lambda i: i.score)
            weights = [rank + 1.0 for rank in range(len(ranked))]
            tournament = random.choices(ranked, weights=weights, k=k)
        else:
            tournament = random.sample(individuals, k)
        return max(tournament, key=lambda i: i.score)

    def pareto_front(self) -> list[Individual]:
        """Return the Pareto-optimal individuals (non-dominated on score + features).

        An individual is dominated if another has >= score and dominates on
        all feature axes (higher is better for diversity purposes).
        """
        individuals = list(self._grid.values())
        if len(individuals) <= 1:
            return list(individuals)

        front: list[Individual] = []
        for candidate in individuals:
            dominated = False
            for other in individuals:
                if other is candidate:
                    continue
                if other.score >= candidate.score and all(
                    o >= c for o, c in zip(other.features, candidate.features)
                ) and (
                    other.score > candidate.score
                    or any(o > c for o, c in zip(other.features, candidate.features))
                ):
                    dominated = True
                    break
            if not dominated:
                front.append(candidate)
        return front

    def diversity_metric(self) -> float:
        """Fraction of occupied cells relative to a reasonable grid size estimate.

        Returns 0.0 for empty archive, approaches 1.0 as more cells are filled.
        """
        if not self._grid:
            return 0.0
        unique_per_axis: list[set[int]] = [set() for _ in range(4)]
        for key in self._grid:
            for i, v in enumerate(key):
                if i < 4:
                    unique_per_axis[i].add(v)
        total_possible = 1
        for s in unique_per_axis:
            total_possible *= max(len(s), 1)
        return len(self._grid) / max(total_possible, 1)

    def save(self, directory: Path) -> None:
        """Serialize the archive to a directory."""
        directory.mkdir(parents=True, exist_ok=True)
        data: dict[str, object] = {}
        for key, ind in self._grid.items():
            str_key = ",".join(str(k) for k in key)
            data[str_key] = ind.model_dump(mode="json")
        (directory / "grid.json").write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, directory: Path) -> MAPElitesArchive:
        """Deserialize an archive from a directory."""
        archive = cls()
        path = directory / "grid.json"
        if path.exists():
            data = json.loads(path.read_text())
            for str_key, ind_data in data.items():
                ind = Individual.model_validate(ind_data)
                archive._grid[ind.features] = ind
        return archive
