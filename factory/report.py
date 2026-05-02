"""Performance report — aggregates CEO verdicts and observations per project."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import structlog

from factory.models import AgentVerdict, Observation, PerformanceReport

log = structlog.get_logger()


def parse_ceo_verdicts(project_path: Path) -> list[AgentVerdict]:
    """Parse all ceo-verdict-*.md files in .factory/reviews/."""
    reviews_dir = project_path / ".factory" / "reviews"
    if not reviews_dir.is_dir():
        return []

    verdicts: list[AgentVerdict] = []
    for verdict_file in sorted(reviews_dir.glob("ceo-verdict-*.md")):
        role = verdict_file.stem.replace("ceo-verdict-", "")
        text = verdict_file.read_text()

        verdict_match = re.search(r"\*\*Verdict:\*\*\s*(PROCEED|REDIRECT|ABORT)", text)
        if not verdict_match:
            continue

        rationale_match = re.search(r"\*\*Rationale:\*\*\s*(.+?)(?:\n|$)", text)
        rationale = rationale_match.group(1).strip() if rationale_match else ""

        issues: list[str] = []
        issues_match = re.search(
            r"\*\*Issues found:\*\*\s*(.+?)(?:\n\*\*|\Z)", text, re.DOTALL
        )
        if issues_match:
            issues_text = issues_match.group(1).strip()
            if issues_text.lower() not in ("none", ""):
                for line in issues_text.splitlines():
                    line = line.strip().lstrip("- ")
                    if line:
                        issues.append(line)

        exp_match = re.search(r"experiment\s+(\d+)", text, re.IGNORECASE)
        exp_id = int(exp_match.group(1)) if exp_match else None

        verdicts.append(AgentVerdict(
            role=role,
            verdict=verdict_match.group(1),  # type: ignore[arg-type]
            rationale=rationale,
            issues=issues,
            experiment_id=exp_id,
        ))

    log.debug("parse_ceo_verdicts", count=len(verdicts), path=str(reviews_dir))
    return verdicts


def parse_observations(project_path: Path) -> list[Observation]:
    """Parse observations from .factory/strategy/observations.md and archive notes."""
    observations: list[Observation] = []
    project_name = project_path.resolve().name

    obs_path = project_path / ".factory" / "strategy" / "observations.md"
    if obs_path.exists():
        text = obs_path.read_text()
        for section in re.split(r"^##\s+", text, flags=re.MULTILINE):
            section = section.strip()
            if not section:
                continue
            lines = section.splitlines()
            title = lines[0].strip()
            content = "\n".join(lines[1:]).strip()
            if content:
                observations.append(Observation(
                    source="observations.md",
                    content=f"{title}: {content[:500]}",
                    timestamp=datetime.now(),
                    project=project_name,
                    tags=["observation"],
                ))

    archive_dir = project_path / ".factory" / "archive"
    if archive_dir.is_dir():
        for note_file in sorted(archive_dir.glob("**/*.md"))[:50]:
            text = note_file.read_text()
            if len(text) > 50:
                observations.append(Observation(
                    source=str(note_file.relative_to(project_path)),
                    content=text[:500],
                    timestamp=datetime.now(),
                    project=project_name,
                    tags=["archive"],
                ))

    log.debug("parse_observations", count=len(observations))
    return observations


def build_performance_report(project_path: Path) -> PerformanceReport:
    """Build a complete performance report for a project."""
    from factory.store import ExperimentStore

    project_name = project_path.resolve().name
    store = ExperimentStore(project_path)

    try:
        import asyncio
        records = asyncio.run(store.load_history())
    except Exception:
        records = []

    keep_count = sum(1 for r in records if r.verdict == "keep")
    revert_count = sum(1 for r in records if r.verdict == "revert")
    error_count = sum(1 for r in records if r.verdict == "error")
    total = len(records)

    scores = [r.score_after for r in records if r.score_after is not None]
    latest_score = scores[-1] if scores else None

    verdicts = parse_ceo_verdicts(project_path)
    observations = parse_observations(project_path)

    verdict_patterns: dict[str, int] = {}
    for v in verdicts:
        key = f"{v.role}:{v.verdict}"
        verdict_patterns[key] = verdict_patterns.get(key, 0) + 1

    return PerformanceReport(
        project_name=project_name,
        generated_at=datetime.now(),
        total_experiments=total,
        keep_count=keep_count,
        revert_count=revert_count,
        error_count=error_count,
        keep_rate=keep_count / total if total > 0 else 0.0,
        latest_score=latest_score,
        agent_verdicts=verdicts,
        observations=observations,
        verdict_patterns=verdict_patterns,
    )


def save_performance_report(project_path: Path) -> Path:
    """Build and save the performance report to .factory/performance_report.json."""
    report = build_performance_report(project_path)
    report_path = project_path / ".factory" / "performance_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.model_dump(), indent=2, default=str) + "\n"
    )
    log.info(
        "performance_report_saved",
        project=report.project_name,
        experiments=report.total_experiments,
        path=str(report_path),
    )
    return report_path


def load_performance_report(project_path: Path) -> PerformanceReport | None:
    """Load an existing performance report, return None if missing."""
    report_path = project_path / ".factory" / "performance_report.json"
    if not report_path.exists():
        return None
    try:
        data = json.loads(report_path.read_text())
        _parse_datetimes(data)
        return PerformanceReport(**data)
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        log.warning("performance_report_load_failed", error=str(exc))
        return None


def _parse_datetimes(data: dict) -> None:
    """Convert ISO datetime strings back to datetime objects for strict Pydantic models."""
    for key in ("generated_at",):
        if key in data and isinstance(data[key], str):
            data[key] = datetime.fromisoformat(data[key])

    for entry in data.get("agent_verdicts", []):
        pass  # no datetime fields

    for obs in data.get("observations", []):
        if "timestamp" in obs and isinstance(obs["timestamp"], str):
            obs["timestamp"] = datetime.fromisoformat(obs["timestamp"])

    for entry in data.get("projects", []):
        for key in ("registered_at", "last_experiment_at"):
            if key in entry and isinstance(entry[key], str):
                entry[key] = datetime.fromisoformat(entry[key])
