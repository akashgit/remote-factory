"""MemPalace API wrappers — the ONLY file that imports from mempalace.*

All mempalace operations are wrapped here with try/except ImportError for
graceful degradation when mempalace is not installed.
"""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path


def get_palace_path() -> str:
    """Return the MemPalace palace directory path."""
    return os.path.expanduser("~/.mempalace/palace")


def get_project_name(project_path: Path) -> str:
    """Return sanitized full resolved path as project identifier."""
    return project_path.resolve().as_posix().replace(" ", "_")


def get_kg():
    """Return a KnowledgeGraph instance. Raises ImportError if mempalace not installed."""
    from mempalace.knowledge_graph import KnowledgeGraph

    return KnowledgeGraph()


# ── Read wrappers ──────────────────────────────────────────────


def search_episodes(palace: str, wing: str, query: str, n_results: int = 5) -> str:
    """Search episodic memory via mempalace.searcher.search. Returns captured stdout."""
    from mempalace.searcher import search

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        search(query, palace, wing=wing, n_results=n_results)
    return buf.getvalue()


def kg_query_entity(
    name: str,
    direction: str = "both",
    as_of: str | None = None,
    kg: object | None = None,
) -> list[dict]:
    """Query KG for entity triples."""
    if kg is None:
        kg = get_kg()
    return kg.query_entity(name, direction=direction, as_of=as_of)  # type: ignore[union-attr]


def kg_timeline(entity_name: str, kg: object | None = None) -> list[dict]:
    """Get temporal timeline for an entity."""
    if kg is None:
        kg = get_kg()
    return kg.timeline(entity_name=entity_name)  # type: ignore[union-attr]


def search_build_outcomes(
    palace: str, wing: str, room: str, query: str, n_results: int = 20
) -> str:
    """Search build outcomes in a specific room. Returns captured stdout."""
    from mempalace.searcher import search

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        search(query, palace, wing=wing, room=room, n_results=n_results)
    return buf.getvalue()


# ── Write wrappers ─────────────────────────────────────────────


def kg_add_triple(subject: str, predicate: str, obj: str, valid_from: str) -> None:
    """Add a temporal KG triple."""
    from mempalace.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph()
    kg.add_triple(subject, predicate, obj, valid_from=valid_from)


def kg_supersede(subject: str, predicate: str, old_obj: str, new_obj: str, at: str) -> None:
    """Supersede a KG triple (marks old as ended, adds new)."""
    from mempalace.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph()
    kg.supersede(subject, predicate, old_obj, new_obj, at=at)


def store_drawer(palace: str, wing: str, room: str, content: str, source_file: str) -> None:
    """Store content as an episodic drawer in the palace."""
    from mempalace.ids import make_drawer_id_from_content
    from mempalace.miner import _build_drawer_metadata
    from mempalace.palace import get_collection

    collection = get_collection(palace, create=True)
    drawer_id = make_drawer_id_from_content(wing, room, content)
    metadata = _build_drawer_metadata(
        wing=wing,
        room=room,
        source_file=source_file,
        chunk_index=0,
        agent="factory",
        content=content,
        source_mtime=None,
    )
    collection.upsert(
        documents=[content],
        ids=[drawer_id],
        metadatas=[metadata],
    )
