"""Knowledge graph models — triplet-based representation of learned facts."""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


# ── entity types ─────────────────────────────────────────────────


class EntityType(str, Enum):
    """Types of entities in the knowledge graph."""

    AGENT = "agent"
    TOOL = "tool"
    ACTION = "action"
    ERROR = "error"
    TASK = "task"
    ENVIRONMENT = "environment"
    CONCEPT = "concept"
    OUTCOME = "outcome"


class Entity(BaseModel):
    """A node in the knowledge graph."""

    model_config = ConfigDict(strict=True, extra="forbid")

    id: str
    type: EntityType
    name: str
    attributes: dict[str, str | float | bool] = {}


# ── predicate types ──────────────────────────────────────────────


class PredicateType(str, Enum):
    """Typed relationships between entities."""

    # behavioral
    CALLS = "calls"
    FAILS_WITH = "fails_with"
    SUCCEEDS_AT = "succeeds_at"
    FAILS_AT = "fails_at"
    PRODUCES = "produces"
    REQUIRES = "requires"
    PRECEDES = "precedes"
    CAUSES = "causes"

    # structural
    IS_A = "is_a"
    PART_OF = "part_of"
    RELATED_TO = "related_to"
    CONTRADICTS = "contradicts"

    # optimization
    IMPROVES = "improves"
    DEGRADES = "degrades"
    CORRELATES_WITH = "correlates_with"


CAUSAL_PREDICATES: frozenset[PredicateType] = frozenset(
    {
        PredicateType.CAUSES,
        PredicateType.FAILS_AT,
        PredicateType.FAILS_WITH,
        PredicateType.REQUIRES,
    }
)


# ── triplet ──────────────────────────────────────────────────────


def _short_uuid() -> str:
    return uuid.uuid4().hex[:16]


class Triplet(BaseModel):
    """A single fact: (subject, predicate, object) with metadata."""

    model_config = ConfigDict(strict=True, extra="forbid")

    id: str = Field(default_factory=_short_uuid)
    subject: Entity
    predicate: PredicateType
    object: Entity
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    source: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)
    evidence: str = ""
    valid_from: datetime | None = None
    valid_until: datetime | None = None


# ── knowledge graph ──────────────────────────────────────────────


class KnowledgeGraph(BaseModel):
    """A collection of triplets scoped to a single task."""

    model_config = ConfigDict(strict=True, extra="forbid")

    task_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    triplets: list[Triplet] = []
    entities: dict[str, Entity] = {}
    metadata: dict[str, str] = {}

    # ── mutation ─────────────────────────────────────────────────

    def add_triplet(self, triplet: Triplet) -> None:
        """Add a triplet and auto-index its entities."""
        self.triplets.append(triplet)
        self.entities[triplet.subject.id] = triplet.subject
        self.entities[triplet.object.id] = triplet.object
        self.updated_at = datetime.now()

    def merge(self, other: KnowledgeGraph) -> None:
        """Merge another graph's triplets into this one."""
        existing_ids = {t.id for t in self.triplets}
        for triplet in other.triplets:
            if triplet.id not in existing_ids:
                self.add_triplet(triplet)

    # ── single-hop queries ───────────────────────────────────────

    def query(
        self,
        subject_id: str | None = None,
        predicate: PredicateType | None = None,
        object_id: str | None = None,
    ) -> list[Triplet]:
        """Filter triplets by any combination of subject, predicate, object."""
        results: list[Triplet] = []
        for t in self.triplets:
            if subject_id is not None and t.subject.id != subject_id:
                continue
            if predicate is not None and t.predicate != predicate:
                continue
            if object_id is not None and t.object.id != object_id:
                continue
            results.append(t)
        return results

    def query_by_subject(self, entity_id: str) -> list[Triplet]:
        return self.query(subject_id=entity_id)

    def query_by_object(self, entity_id: str) -> list[Triplet]:
        return self.query(object_id=entity_id)

    def query_by_predicate(self, predicate: PredicateType) -> list[Triplet]:
        return self.query(predicate=predicate)

    def related_entities(self, entity_id: str) -> list[Entity]:
        """All entities connected to entity_id as either subject or object."""
        seen: set[str] = set()
        result: list[Entity] = []
        for t in self.triplets:
            if t.subject.id == entity_id and t.object.id not in seen:
                seen.add(t.object.id)
                result.append(t.object)
            elif t.object.id == entity_id and t.subject.id not in seen:
                seen.add(t.subject.id)
                result.append(t.subject)
        return result

    def subgraph(self, entity_ids: set[str]) -> KnowledgeGraph:
        """Extract a sub-graph containing only triplets involving the given entities."""
        filtered = [
            t for t in self.triplets if t.subject.id in entity_ids or t.object.id in entity_ids
        ]
        graph = KnowledgeGraph(
            task_id=self.task_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            metadata=self.metadata,
        )
        for t in filtered:
            graph.add_triplet(t)
        return graph

    # ── multi-hop traversal ──────────────────────────────────────

    def _adjacency(
        self,
        allowed_predicates: frozenset[PredicateType] | None = None,
    ) -> dict[str, list[tuple[Triplet, str]]]:
        """Build adjacency list: entity_id -> [(triplet, neighbor_id)]."""
        adj: dict[str, list[tuple[Triplet, str]]] = defaultdict(list)
        for t in self.triplets:
            if allowed_predicates and t.predicate not in allowed_predicates:
                continue
            adj[t.subject.id].append((t, t.object.id))
            adj[t.object.id].append((t, t.subject.id))
        return adj

    def traverse(
        self,
        entity_id: str,
        max_hops: int = 3,
    ) -> list[list[Triplet]]:
        """BFS from an entity, returning all paths as triplet chains up to max_hops."""
        adj = self._adjacency()
        paths: list[list[Triplet]] = []
        queue: deque[tuple[str, list[Triplet]]] = deque([(entity_id, [])])
        visited_paths: set[tuple[str, ...]] = set()

        while queue:
            current, path = queue.popleft()
            if len(path) >= max_hops:
                continue

            for triplet, neighbor in adj.get(current, []):
                if triplet.id in {t.id for t in path}:
                    continue

                new_path = [*path, triplet]
                path_key = tuple(t.id for t in new_path)
                if path_key in visited_paths:
                    continue
                visited_paths.add(path_key)
                paths.append(new_path)
                queue.append((neighbor, new_path))

        return paths

    def find_paths(
        self,
        from_id: str,
        to_id: str,
        max_hops: int = 5,
    ) -> list[list[Triplet]]:
        """All paths between two entities, up to max_hops."""
        adj = self._adjacency()
        results: list[list[Triplet]] = []
        queue: deque[tuple[str, list[Triplet], set[str]]] = deque([(from_id, [], {from_id})])

        while queue:
            current, path, visited = queue.popleft()
            if len(path) >= max_hops:
                continue

            for triplet, neighbor in adj.get(current, []):
                if neighbor in visited:
                    continue

                new_path = [*path, triplet]
                if neighbor == to_id:
                    results.append(new_path)
                    continue

                queue.append((neighbor, new_path, visited | {neighbor}))

        return results

    def causal_chain(
        self,
        entity_id: str,
        max_depth: int = 5,
    ) -> list[list[Triplet]]:
        """Follow only causal predicates to build explanation chains."""
        adj = self._adjacency(allowed_predicates=CAUSAL_PREDICATES)
        chains: list[list[Triplet]] = []
        queue: deque[tuple[str, list[Triplet], set[str]]] = deque([(entity_id, [], {entity_id})])

        while queue:
            current, path, visited = queue.popleft()
            if len(path) >= max_depth:
                if path:
                    chains.append(path)
                continue

            neighbors = adj.get(current, [])
            if not neighbors and path:
                chains.append(path)
                continue

            extended = False
            for triplet, neighbor in neighbors:
                if neighbor in visited:
                    continue
                extended = True
                queue.append((neighbor, [*path, triplet], visited | {neighbor}))

            if not extended and path:
                chains.append(path)

        return chains

    def match_pattern(
        self,
        steps: list[tuple[str | None, PredicateType | None, str | None]],
    ) -> list[list[Triplet]]:
        """Match a multi-hop structural pattern with wildcards (None = any).

        Each step is (subject_id_or_None, predicate_or_None, object_id_or_None).
        Adjacent steps are connected: step[i].object matches step[i+1].subject.
        """
        if not steps:
            return []

        def _matches(
            triplet: Triplet,
            pattern: tuple[str | None, PredicateType | None, str | None],
        ) -> bool:
            s, p, o = pattern
            if s is not None and triplet.subject.id != s:
                return False
            if p is not None and triplet.predicate != p:
                return False
            if o is not None and triplet.object.id != o:
                return False
            return True

        first_step = steps[0]
        candidates = [t for t in self.triplets if _matches(t, first_step)]

        paths: list[list[Triplet]] = [[c] for c in candidates]

        for step in steps[1:]:
            next_paths: list[list[Triplet]] = []
            for path in paths:
                last_object_id = path[-1].object.id
                for t in self.triplets:
                    if t.subject.id != last_object_id:
                        continue
                    if not _matches(t, (None, step[1], step[2])):
                        continue
                    next_paths.append([*path, t])
            paths = next_paths

        return paths

    # ── stats ────────────────────────────────────────────────────

    def entity_count(self) -> int:
        return len(self.entities)

    def triplet_count(self) -> int:
        return len(self.triplets)

    def stats(self) -> dict[str, object]:
        """Summary statistics: degree ranking, predicate distribution, failure hotspots."""
        degree: dict[str, int] = defaultdict(int)
        predicate_counts: dict[str, int] = defaultdict(int)
        failure_entities: dict[str, int] = defaultdict(int)

        for t in self.triplets:
            degree[t.subject.id] += 1
            degree[t.object.id] += 1
            predicate_counts[t.predicate.value] += 1

            if t.predicate in (PredicateType.FAILS_WITH, PredicateType.FAILS_AT):
                failure_entities[t.subject.id] += 1
                failure_entities[t.object.id] += 1

        top_entities = sorted(degree.items(), key=lambda x: x[1], reverse=True)[:10]
        top_failures = sorted(failure_entities.items(), key=lambda x: x[1], reverse=True)[:10]

        return {
            "entity_count": self.entity_count(),
            "triplet_count": self.triplet_count(),
            "top_entities_by_degree": top_entities,
            "predicate_distribution": dict(predicate_counts),
            "failure_hotspots": top_failures,
        }
