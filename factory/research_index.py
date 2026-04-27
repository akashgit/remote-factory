"""Research citation index — tracks which experiments cite research sources."""

import csv
import json
import re
from pathlib import Path

import structlog

log = structlog.get_logger()

_URL_RE = re.compile(r"https?://[^\s),]+")
_ISSUE_RE = re.compile(r"#(\d+)")
_ARXIV_RE = re.compile(r"arxiv[:/](\d{4}\.\d{4,5})", re.IGNORECASE)


def extract_citations(text: str) -> list[str]:
    """Extract URLs, GitHub issue refs, and arxiv IDs from text."""
    citations: list[str] = []
    citations.extend(_URL_RE.findall(text))
    for m in _ISSUE_RE.finditer(text):
        citations.append(f"#{m.group(1)}")
    for m in _ARXIV_RE.finditer(text):
        citations.append(f"arxiv:{m.group(1)}")
    return sorted(set(citations))


def _load_backfill(project_path: Path) -> dict[str, list[str]]:
    """Load backfilled citations from .factory/citations.json."""
    path = project_path / ".factory" / "citations.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return {str(k): v for k, v in data.items()}
    except (json.JSONDecodeError, OSError):
        return {}


def backfill_citations(project_path: Path) -> dict[str, list[str]]:
    """Scan experiments and extract citations from hypothesis/notes text.

    Writes results to .factory/citations.json and returns the index.
    """
    tsv_path = project_path / ".factory" / "results.tsv"
    if not tsv_path.exists():
        return {}

    index: dict[str, list[str]] = {}
    with open(tsv_path, newline="") as f:
        reader = csv.DictReader(f, dialect="excel-tab")
        for row in reader:
            exp_id = row["id"]
            text = " ".join([
                row.get("hypothesis", ""),
                row.get("change_summary", ""),
                row.get("notes", ""),
            ])
            citations = extract_citations(text)
            if citations:
                index[exp_id] = citations

    out_path = project_path / ".factory" / "citations.json"
    out_path.write_text(json.dumps(index, indent=2) + "\n")
    log.info(
        "citations_backfilled",
        total_experiments=len(index),
        output=str(out_path),
    )
    return index


def _load_citations_from_tsv(project_path: Path) -> list[tuple[int, list[str]]]:
    """Parse results.tsv and return list of (experiment_id, citations) tuples."""
    tsv_path = project_path / ".factory" / "results.tsv"
    if not tsv_path.exists():
        return []

    backfill = _load_backfill(project_path)

    results: list[tuple[int, list[str]]] = []
    with open(tsv_path, newline="") as f:
        reader = csv.DictReader(f, dialect="excel-tab")
        for row in reader:
            exp_id = int(row["id"])
            raw = row.get("research_citations", "")
            citations = [c.strip() for c in raw.split("|") if c.strip()] if raw else []
            if not citations:
                citations = backfill.get(str(exp_id), [])
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
