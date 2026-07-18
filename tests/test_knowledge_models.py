"""Tests for factory.knowledge.models — knowledge graph models and traversal."""

from datetime import datetime

import pytest

from factory.knowledge.models import (
    CAUSAL_PREDICATES,
    Entity,
    EntityType,
    KnowledgeGraph,
    PredicateType,
    Triplet,
)


# ── helpers ──────────────────────────────────────────────────────


def _entity(etype: EntityType, name: str) -> Entity:
    slug = name.lower().replace(" ", "_")
    return Entity(id=f"{etype.value}:{slug}", type=etype, name=name)


def _triplet(
    subj: Entity,
    pred: PredicateType,
    obj: Entity,
    confidence: float = 1.0,
    source: str = "test",
) -> Triplet:
    return Triplet(
        subject=subj,
        predicate=pred,
        object=obj,
        confidence=confidence,
        source=source,
        timestamp=datetime(2026, 1, 1),
    )


AGENT = _entity(EntityType.AGENT, "main_agent")
TOOL_A = _entity(EntityType.TOOL, "get_order")
TOOL_B = _entity(EntityType.TOOL, "cancel_booking")
ERROR = _entity(EntityType.ERROR, "missing_param")
TASK = _entity(EntityType.TASK, "cancel_order")
OUTCOME = _entity(EntityType.OUTCOME, "success")
CONCEPT = _entity(EntityType.CONCEPT, "booking_id")


# ── entity tests ─────────────────────────────────────────────────


class TestEntity:
    def test_construction(self):
        e = Entity(id="tool:foo", type=EntityType.TOOL, name="foo")
        assert e.id == "tool:foo"
        assert e.type == EntityType.TOOL

    def test_with_attributes(self):
        e = Entity(
            id="tool:bar",
            type=EntityType.TOOL,
            name="bar",
            attributes={"count": 5.0, "active": True},
        )
        assert e.attributes["count"] == 5.0

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            Entity(id="x", type=EntityType.TOOL, name="x", bogus="nope")  # type: ignore[call-arg]


# ── triplet tests ────────────────────────────────────────────────


class TestTriplet:
    def test_auto_id(self):
        t = _triplet(AGENT, PredicateType.CALLS, TOOL_A)
        assert len(t.id) == 16

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            Triplet(
                subject=AGENT,
                predicate=PredicateType.CALLS,
                object=TOOL_A,
                confidence=1.5,
                timestamp=datetime(2026, 1, 1),
            )

    def test_serialization_roundtrip(self):
        t = _triplet(AGENT, PredicateType.CALLS, TOOL_A)
        data = t.model_dump(mode="json")
        restored = Triplet.model_validate(data, strict=False)
        assert restored.subject.id == t.subject.id
        assert restored.predicate == t.predicate


# ── knowledge graph: mutation ────────────────────────────────────


class TestKnowledgeGraphMutation:
    def test_add_triplet(self):
        g = KnowledgeGraph(task_id="test")
        t = _triplet(AGENT, PredicateType.CALLS, TOOL_A)
        g.add_triplet(t)
        assert g.triplet_count() == 1
        assert g.entity_count() == 2
        assert AGENT.id in g.entities
        assert TOOL_A.id in g.entities

    def test_merge_deduplicates(self):
        g1 = KnowledgeGraph(task_id="test")
        g2 = KnowledgeGraph(task_id="test")
        t = _triplet(AGENT, PredicateType.CALLS, TOOL_A)
        g1.add_triplet(t)
        g2.add_triplet(t)
        g2.add_triplet(_triplet(AGENT, PredicateType.CALLS, TOOL_B))
        g1.merge(g2)
        assert g1.triplet_count() == 2


# ── knowledge graph: single-hop queries ──────────────────────────


def _sample_graph() -> KnowledgeGraph:
    g = KnowledgeGraph(task_id="test")
    g.add_triplet(_triplet(AGENT, PredicateType.CALLS, TOOL_A))
    g.add_triplet(_triplet(AGENT, PredicateType.CALLS, TOOL_B))
    g.add_triplet(_triplet(AGENT, PredicateType.FAILS_AT, TASK))
    g.add_triplet(_triplet(TOOL_B, PredicateType.FAILS_WITH, ERROR))
    g.add_triplet(_triplet(TASK, PredicateType.REQUIRES, CONCEPT))
    g.add_triplet(_triplet(AGENT, PredicateType.SUCCEEDS_AT, OUTCOME))
    return g


class TestSingleHopQueries:
    def test_query_by_subject(self):
        g = _sample_graph()
        results = g.query_by_subject(AGENT.id)
        assert len(results) == 4

    def test_query_by_object(self):
        g = _sample_graph()
        results = g.query_by_object(TOOL_A.id)
        assert len(results) == 1

    def test_query_by_predicate(self):
        g = _sample_graph()
        results = g.query_by_predicate(PredicateType.CALLS)
        assert len(results) == 2

    def test_query_combined(self):
        g = _sample_graph()
        results = g.query(
            subject_id=AGENT.id,
            predicate=PredicateType.CALLS,
        )
        assert len(results) == 2

    def test_query_no_match(self):
        g = _sample_graph()
        results = g.query(subject_id="nonexistent:x")
        assert results == []

    def test_related_entities(self):
        g = _sample_graph()
        related = g.related_entities(AGENT.id)
        related_ids = {e.id for e in related}
        assert TOOL_A.id in related_ids
        assert TOOL_B.id in related_ids
        assert TASK.id in related_ids

    def test_subgraph(self):
        g = _sample_graph()
        sub = g.subgraph({AGENT.id, TOOL_A.id})
        assert sub.triplet_count() >= 1
        for t in sub.triplets:
            assert t.subject.id in {AGENT.id, TOOL_A.id} or t.object.id in {
                AGENT.id,
                TOOL_A.id,
            }


# ── knowledge graph: multi-hop traversal ─────────────────────────


def _chain_graph() -> KnowledgeGraph:
    """A -> B -> C -> D linear chain for traversal tests."""
    g = KnowledgeGraph(task_id="chain")
    a = _entity(EntityType.AGENT, "A")
    b = _entity(EntityType.TASK, "B")
    c = _entity(EntityType.TOOL, "C")
    d = _entity(EntityType.ERROR, "D")
    g.add_triplet(_triplet(a, PredicateType.FAILS_AT, b))
    g.add_triplet(_triplet(b, PredicateType.REQUIRES, c))
    g.add_triplet(_triplet(c, PredicateType.FAILS_WITH, d))
    return g


class TestTraverse:
    def test_traverse_depth_1(self):
        g = _chain_graph()
        paths = g.traverse("agent:a", max_hops=1)
        assert len(paths) == 1
        assert paths[0][0].subject.id == "agent:a"

    def test_traverse_depth_3(self):
        g = _chain_graph()
        paths = g.traverse("agent:a", max_hops=3)
        assert any(len(p) == 3 for p in paths)

    def test_traverse_nonexistent(self):
        g = _chain_graph()
        paths = g.traverse("nonexistent:x", max_hops=3)
        assert paths == []


class TestFindPaths:
    def test_direct_path(self):
        g = _chain_graph()
        paths = g.find_paths("agent:a", "task:b")
        assert len(paths) == 1
        assert len(paths[0]) == 1

    def test_multi_hop_path(self):
        g = _chain_graph()
        paths = g.find_paths("agent:a", "error:d")
        assert len(paths) >= 1
        assert any(len(p) == 3 for p in paths)

    def test_no_path(self):
        g = _chain_graph()
        e = _entity(EntityType.CONCEPT, "isolated")
        g.add_triplet(_triplet(e, PredicateType.IS_A, e))
        paths = g.find_paths("agent:a", e.id)
        assert paths == []


class TestCausalChain:
    def test_follows_causal_predicates(self):
        g = _chain_graph()
        chains = g.causal_chain("agent:a")
        assert len(chains) >= 1
        for chain in chains:
            for t in chain:
                assert t.predicate in CAUSAL_PREDICATES

    def test_stops_at_non_causal(self):
        g = KnowledgeGraph(task_id="test")
        a = _entity(EntityType.AGENT, "A")
        b = _entity(EntityType.TASK, "B")
        c = _entity(EntityType.TOOL, "C")
        g.add_triplet(_triplet(a, PredicateType.FAILS_WITH, b))
        g.add_triplet(_triplet(b, PredicateType.CALLS, c))
        chains = g.causal_chain("agent:a")
        for chain in chains:
            assert all(t.predicate in CAUSAL_PREDICATES for t in chain)


class TestMatchPattern:
    def test_single_step(self):
        g = _sample_graph()
        matches = g.match_pattern([(AGENT.id, PredicateType.CALLS, None)])
        assert len(matches) == 2

    def test_two_step_pattern(self):
        g = _sample_graph()
        matches = g.match_pattern(
            [
                (AGENT.id, PredicateType.FAILS_AT, None),
                (None, PredicateType.REQUIRES, None),
            ]
        )
        assert len(matches) == 1
        assert matches[0][0].object.id == TASK.id
        assert matches[0][1].object.id == CONCEPT.id

    def test_no_match(self):
        g = _sample_graph()
        matches = g.match_pattern(
            [
                (AGENT.id, PredicateType.IMPROVES, None),
            ]
        )
        assert matches == []

    def test_empty_pattern(self):
        g = _sample_graph()
        assert g.match_pattern([]) == []


# ── stats ────────────────────────────────────────────────────────


class TestStats:
    def test_stats_structure(self):
        g = _sample_graph()
        s = g.stats()
        assert "entity_count" in s
        assert "triplet_count" in s
        assert "top_entities_by_degree" in s
        assert "predicate_distribution" in s
        assert "failure_hotspots" in s
        assert s["entity_count"] == g.entity_count()
        assert s["triplet_count"] == g.triplet_count()

    def test_failure_hotspots(self):
        g = _sample_graph()
        s = g.stats()
        hotspot_ids = {eid for eid, _ in s["failure_hotspots"]}  # type: ignore[union-attr]
        assert AGENT.id in hotspot_ids or ERROR.id in hotspot_ids
