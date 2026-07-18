"""Knowledge graph persistence — file-based store at .factory/knowledge/."""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from filelock import FileLock

from factory.knowledge.insight import Insight
from factory.knowledge.models import KnowledgeGraph, Triplet

log = structlog.get_logger()


class KnowledgeStore:
    """Manages knowledge graph persistence at .factory/knowledge/."""

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path
        self.knowledge_dir = project_path / ".factory" / "knowledge"
        self._lock = FileLock(self.knowledge_dir / ".knowledge.lock")

    async def init(self) -> None:
        """Create .factory/knowledge/ directory structure."""
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        log.info("knowledge_store_init", path=str(self.knowledge_dir))

    def _graph_path(self, task_id: str) -> Path:
        return self.knowledge_dir / f"{task_id}.json"

    def _insights_path(self, task_id: str) -> Path:
        return self.knowledge_dir / f"{task_id}_insights.json"

    async def save_graph(self, graph: KnowledgeGraph) -> None:
        """Persist the knowledge graph to .factory/knowledge/{task_id}.json."""
        await self.init()
        path = self._graph_path(graph.task_id)
        data = graph.model_dump(mode="json")
        with self._lock:
            path.write_text(json.dumps(data, indent=2, default=str))
        log.info(
            "knowledge_graph_saved",
            task_id=graph.task_id,
            triplets=graph.triplet_count(),
            entities=graph.entity_count(),
        )

    async def load_graph(self, task_id: str) -> KnowledgeGraph | None:
        """Load a knowledge graph by task_id. Returns None if not found."""
        path = self._graph_path(task_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return KnowledgeGraph.model_validate(data, strict=False)
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("knowledge_graph_load_failed", task_id=task_id, error=str(exc))
            return None

    async def list_graphs(self) -> list[str]:
        """List all task_ids with stored knowledge graphs."""
        if not self.knowledge_dir.exists():
            return []
        return [
            p.stem for p in self.knowledge_dir.glob("*.json") if not p.stem.endswith("_insights")
        ]

    async def append_triplets(
        self,
        task_id: str,
        triplets: list[Triplet],
    ) -> KnowledgeGraph:
        """Add triplets to an existing graph (or create a new one).

        Loads the graph, adds triplets, re-indexes entities, saves.
        Uses filelock for concurrency safety.
        """
        await self.init()
        graph = await self.load_graph(task_id)
        if graph is None:
            graph = KnowledgeGraph(task_id=task_id)

        existing_ids = {t.id for t in graph.triplets}
        added = 0
        for triplet in triplets:
            if triplet.id not in existing_ids:
                graph.add_triplet(triplet)
                existing_ids.add(triplet.id)
                added += 1

        await self.save_graph(graph)
        log.info(
            "knowledge_triplets_appended",
            task_id=task_id,
            added=added,
            total=graph.triplet_count(),
        )
        return graph

    async def save_insights(
        self,
        task_id: str,
        insights: list[Insight],
    ) -> None:
        """Save generated insights alongside the graph."""
        await self.init()
        path = self._insights_path(task_id)
        data = [i.model_dump(mode="json") for i in insights]
        with self._lock:
            path.write_text(json.dumps(data, indent=2, default=str))
        log.info("knowledge_insights_saved", task_id=task_id, count=len(insights))

    async def load_insights(self, task_id: str) -> list[Insight]:
        """Load previously generated insights."""
        path = self._insights_path(task_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text())
            return [Insight.model_validate(item, strict=False) for item in data]
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("knowledge_insights_load_failed", task_id=task_id, error=str(exc))
            return []

    async def export_graph(
        self,
        task_id: str,
        fmt: str = "json",
    ) -> str:
        """Export the graph in the requested format (json, markdown, or dot)."""
        graph = await self.load_graph(task_id)
        if graph is None:
            return ""

        if fmt == "json":
            return json.dumps(graph.model_dump(mode="json"), indent=2, default=str)

        if fmt == "markdown":
            return _export_markdown(graph)

        if fmt == "dot":
            return _export_dot(graph)

        log.warning("knowledge_export_unknown_format", fmt=fmt)
        return ""


def _export_markdown(graph: KnowledgeGraph) -> str:
    lines = [
        f"# Knowledge Graph: {graph.task_id}",
        "",
        f"**Entities:** {graph.entity_count()} | **Triplets:** {graph.triplet_count()}",
        "",
        "| Subject | Predicate | Object | Confidence | Source |",
        "|---------|-----------|--------|------------|--------|",
    ]
    for t in graph.triplets:
        lines.append(
            f"| {t.subject.name} | {t.predicate.value} | {t.object.name} "
            f"| {t.confidence:.2f} | {t.source} |"
        )
    return "\n".join(lines)


def _export_dot(graph: KnowledgeGraph) -> str:
    lines = ["digraph knowledge {", "  rankdir=LR;", "  node [shape=box];"]
    for entity in graph.entities.values():
        label = f"{entity.name}\\n({entity.type.value})"
        lines.append(f'  "{entity.id}" [label="{label}"];')
    for t in graph.triplets:
        label = t.predicate.value
        if t.confidence < 1.0:
            label += f" ({t.confidence:.2f})"
        lines.append(f'  "{t.subject.id}" -> "{t.object.id}" [label="{label}"];')
    lines.append("}")
    return "\n".join(lines)
