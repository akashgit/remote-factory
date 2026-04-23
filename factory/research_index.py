"""Research citation index — tracks which experiments cite research sources."""

import csv
from pathlib import Path

import structlog

log = structlog.get_logger()


def _load_citations_from_tsv(project_path: Path) -> list[tuple[int, list[str]]]:
    """Parse results.tsv and return list of (experiment_id, citations) tuples."""
    tsv_path = project_path / ".factory" / "results.tsv"
    if not tsv_path.exists():
        return []

    results: list[tuple[int, list[str]]] = []
    with open(tsv_path, newline="") as f:
        reader = csv.DictReader(f, dialect="excel-tab")
        for row in reader:
            exp_id = int(row["id"])
            raw = row.get("research_citations", "")
            citations = [c.strip() for c in raw.split("|") if c.strip()] if raw else []
            results.append((exp_id, citations))
    return results


def build_citation_index(project_path: Path) -> dict[int, list[str]]:
    """Load experiment history and return mapping of experiment_id to list of citations."""
    all_rows = _load_citations_from_tsv(project_path)
    index: dict[int, list[str]] = {}
    for exp_id, citations in all_rows:
        if citations:
            index[exp_id] = citations
    log.debug(
        "citation_index_built",
        total_experiments=len(all_rows),
        cited_experiments=len(index),
    )
    return index


def citation_coverage(project_path: Path) -> float:
    """Return fraction of recent experiments (last 10) with at least one citation."""
    all_rows = _load_citations_from_tsv(project_path)
    if not all_rows:
        return 0.0
    recent = all_rows[-10:]
    cited = sum(1 for _, citations in recent if citations)
    coverage = cited / len(recent)
    log.debug(
        "citation_coverage_computed",
        recent_count=len(recent),
        cited_count=cited,
        coverage=coverage,
    )
    return coverage


def uncited_experiments(project_path: Path) -> list[int]:
    """Return experiment IDs without citations from recent history (last 10)."""
    all_rows = _load_citations_from_tsv(project_path)
    if not all_rows:
        return []
    recent = all_rows[-10:]
    uncited = [exp_id for exp_id, citations in recent if not citations]
    log.debug("uncited_experiments_found", count=len(uncited))
    return uncited
