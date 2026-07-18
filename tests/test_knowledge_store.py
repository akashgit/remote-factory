"""Tests for factory.knowledge.store — knowledge graph persistence."""

from datetime import datetime
from pathlib import Path

import pytest

from factory.knowledge.insight import Insight, InsightType
from factory.knowledge.models import (
    Entity,
    EntityType,
    KnowledgeGraph,
    PredicateType,
    Triplet,
)
from factory.knowledge.store import KnowledgeStore


# ── helpers ──────────────────────────────────────────────────────


def _entity(etype: EntityType, name: str) -> Entity:
    slug = name.lower().replace(" ", "_")
    return Entity(id=f"{etype.value}:{slug}", type=etype, name=name)


def _triplet(subj: Entity, pred: PredicateType, obj: Entity) -> Triplet:
    return Triplet(
        subject=subj,
        predicate=pred,
        object=obj,
        source="test",
        timestamp=datetime(2026, 1, 1),
    )


def _sample_graph(task_id: str = "task_1") -> KnowledgeGraph:
    agent = _entity(EntityType.AGENT, "main")
    tool = _entity(EntityType.TOOL, "get_order")
    error = _entity(EntityType.ERROR, "timeout")
    g = KnowledgeGraph(task_id=task_id)
    g.add_triplet(_triplet(agent, PredicateType.CALLS, tool))
    g.add_triplet(_triplet(tool, PredicateType.FAILS_WITH, error))
    return g


# ── store tests ──────────────────────────────────────────────────


class TestKnowledgeStoreInit:
    @pytest.mark.asyncio
    async def test_init_creates_directory(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        await store.init()
        assert (tmp_path / ".factory" / "knowledge").is_dir()

    @pytest.mark.asyncio
    async def test_init_idempotent(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        await store.init()
        await store.init()
        assert (tmp_path / ".factory" / "knowledge").is_dir()


class TestKnowledgeStoreSaveLoad:
    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        graph = _sample_graph()
        await store.save_graph(graph)
        loaded = await store.load_graph("task_1")
        assert loaded is not None
        assert loaded.task_id == "task_1"
        assert loaded.triplet_count() == 2
        assert loaded.entity_count() == 3

    @pytest.mark.asyncio
    async def test_load_nonexistent(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        await store.init()
        result = await store.load_graph("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_corrupt_file(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        await store.init()
        bad_path = tmp_path / ".factory" / "knowledge" / "bad.json"
        bad_path.write_text("not json{{{")
        result = await store.load_graph("bad")
        assert result is None


class TestKnowledgeStoreAppend:
    @pytest.mark.asyncio
    async def test_append_to_new_graph(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        agent = _entity(EntityType.AGENT, "main")
        tool = _entity(EntityType.TOOL, "search")
        triplets = [_triplet(agent, PredicateType.CALLS, tool)]
        graph = await store.append_triplets("new_task", triplets)
        assert graph.triplet_count() == 1
        assert graph.task_id == "new_task"

    @pytest.mark.asyncio
    async def test_append_to_existing_graph(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        graph = _sample_graph()
        await store.save_graph(graph)

        new_triplet = _triplet(
            _entity(EntityType.AGENT, "main"),
            PredicateType.SUCCEEDS_AT,
            _entity(EntityType.TASK, "checkout"),
        )
        updated = await store.append_triplets("task_1", [new_triplet])
        assert updated.triplet_count() == 3

    @pytest.mark.asyncio
    async def test_append_deduplicates(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        graph = _sample_graph()
        await store.save_graph(graph)

        existing_triplet = graph.triplets[0]
        updated = await store.append_triplets("task_1", [existing_triplet])
        assert updated.triplet_count() == 2


class TestKnowledgeStoreList:
    @pytest.mark.asyncio
    async def test_list_empty(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        result = await store.list_graphs()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_multiple(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        await store.save_graph(_sample_graph("alpha"))
        await store.save_graph(_sample_graph("beta"))
        result = await store.list_graphs()
        assert sorted(result) == ["alpha", "beta"]

    @pytest.mark.asyncio
    async def test_list_excludes_insights(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        await store.save_graph(_sample_graph("task_1"))
        await store.save_insights(
            "task_1",
            [
                Insight(
                    type=InsightType.FAILURE_PATTERN,
                    title="test",
                    description="test insight",
                ),
            ],
        )
        result = await store.list_graphs()
        assert result == ["task_1"]


class TestKnowledgeStoreInsights:
    @pytest.mark.asyncio
    async def test_save_and_load_insights(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        insights = [
            Insight(
                type=InsightType.FAILURE_PATTERN,
                title="Repeated timeout",
                description="The agent times out on get_order calls",
                confidence=0.9,
                evidence_triplet_ids=["abc123"],
                suggested_action="Increase timeout or add retry logic",
            ),
        ]
        await store.save_insights("task_1", insights)
        loaded = await store.load_insights("task_1")
        assert len(loaded) == 1
        assert loaded[0].title == "Repeated timeout"
        assert loaded[0].type == InsightType.FAILURE_PATTERN

    @pytest.mark.asyncio
    async def test_load_insights_empty(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        await store.init()
        result = await store.load_insights("nonexistent")
        assert result == []


class TestKnowledgeStoreExport:
    @pytest.mark.asyncio
    async def test_export_json(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        await store.save_graph(_sample_graph())
        output = await store.export_graph("task_1", fmt="json")
        assert '"task_id": "task_1"' in output

    @pytest.mark.asyncio
    async def test_export_markdown(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        await store.save_graph(_sample_graph())
        output = await store.export_graph("task_1", fmt="markdown")
        assert "# Knowledge Graph: task_1" in output
        assert "| Subject |" in output

    @pytest.mark.asyncio
    async def test_export_dot(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        await store.save_graph(_sample_graph())
        output = await store.export_graph("task_1", fmt="dot")
        assert "digraph knowledge" in output
        assert "->" in output

    @pytest.mark.asyncio
    async def test_export_nonexistent(self, tmp_path: Path):
        store = KnowledgeStore(tmp_path)
        await store.init()
        output = await store.export_graph("nonexistent")
        assert output == ""
