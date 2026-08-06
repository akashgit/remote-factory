"""MemPalace read operations — called from study_project_local()."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from filelock import FileLock

from .helpers import (
    get_kg,
    get_palace_path,
    get_project_name,
    kg_query_entity,
    kg_timeline,
    search_build_outcomes,
    search_episodes,
)


def _extract_task_terms(task_hint: str, max_terms: int = 5) -> list[str]:
    """Extract meaningful terms from task_hint for KG queries (lowercase, len >= 4)."""
    return [w for w in task_hint.lower().split() if len(w) >= 4][:max_terms]


def mp_read(project_path: Path, task_hint: str | None = None) -> str:
    """Read MemPalace context: episodic search + KG query + timeline + build outcomes.

    No-op if mempalace not installed.
    """
    try:
        from mempalace.searcher import search  # noqa: F401
    except ImportError:
        return ""

    pn = get_project_name(project_path)
    palace = get_palace_path()

    with FileLock(project_path / ".factory/.mempalace.lock"):
        memory_dir = project_path / ".factory/archive/memory"
        memory_dir.mkdir(parents=True, exist_ok=True)

        if task_hint:
            query = task_hint
        else:
            obs = project_path / ".factory/strategy/observations.md"
            query = " ".join(obs.read_text().split("\n")[:5]) if obs.exists() else pn

        ep = memory_dir / "episodes.md"
        try:
            ep.write_text(search_episodes(palace, wing="project:" + pn, query=query, n_results=5))
        except Exception:
            ep.write_text("")

        anti = memory_dir / "anti-patterns.md"
        try:
            anti_query = "failed reverted broken"
            if task_hint:
                anti_query += " " + task_hint
            anti.write_text(
                search_build_outcomes(
                    palace, wing="project:" + pn, room="failures",
                    query=anti_query, n_results=5,
                )
            )
        except Exception:
            anti.write_text("")

        reviews_f = memory_dir / "reviews.md"
        try:
            reviews_query = task_hint if task_hint else "code review issues findings"
            reviews_f.write_text(search_build_outcomes(
                palace, wing="project:" + pn, room="reviews",
                query=reviews_query, n_results=10,
            ))
        except Exception:
            reviews_f.write_text("")

        decisions_f = memory_dir / "decisions.md"
        try:
            decisions_query = task_hint if task_hint else "decision rationale tradeoff"
            decisions_f.write_text(search_build_outcomes(
                palace, wing="project:" + pn, room="decisions",
                query=decisions_query, n_results=10,
            ))
        except Exception:
            decisions_f.write_text("")

        try:
            shared_kg = get_kg()
        except ImportError:
            shared_kg = None

        fk = memory_dir / "facts.md"
        try:
            rows = kg_query_entity(pn, direction="both", as_of=date.today().isoformat(), kg=shared_kg)
            lines: list[str] = [
                str(r["subject"]) + " " + str(r["predicate"]) + " " + str(r["object"])
                for r in rows
            ]
            if task_hint and shared_kg is not None:
                for term in _extract_task_terms(task_hint):
                    try:
                        term_rows = kg_query_entity(
                            term, direction="both", as_of=date.today().isoformat(), kg=shared_kg,
                        )
                        lines.extend(
                            str(r["subject"]) + " " + str(r["predicate"]) + " " + str(r["object"])
                            for r in term_rows
                        )
                    except Exception:
                        continue
            fk.write_text("\n".join(lines))
        except Exception:
            fk.write_text("")

        tl_f = memory_dir / "timeline.md"
        try:
            tl = kg_timeline(entity_name=pn, kg=shared_kg)
            tl_f.write_text("\n".join(
                str(r["valid_from"]) + ": " + str(r["subject"]) + " " + str(r["predicate"]) + " " + str(r["object"])
                for r in tl
            ))
        except Exception:
            tl_f.write_text("")

        outcomes_query = task_hint if task_hint else "experiment verdict keep revert"
        outcomes = memory_dir / "outcomes.md"
        try:
            outcomes.write_text(search_build_outcomes(
                palace, wing="project:" + pn, room="experiments",
                query=outcomes_query, n_results=20,
            ))
        except Exception:
            outcomes.write_text("")

        content = (
            "## Episodic Memory (Task-Relevant)\n" + ep.read_text()
            + "\n\n## Past QA Findings\n" + reviews_f.read_text()
            + "\n\n## Design Rationale\n" + decisions_f.read_text()
            + "\n\n## Anti-Patterns & Past Failures\n" + anti.read_text()
            + "\n\n## Knowledge Graph Facts\n" + fk.read_text()
            + "\n\n## Timeline\n" + tl_f.read_text()
            + "\n\n## Experiment Outcomes\n" + outcomes.read_text()
        )
        ctx = memory_dir / "context.md"
        ctx.write_text(content)
    return content
