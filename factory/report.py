"""Performance report — aggregates CEO verdicts and observations per project."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

import structlog
from jinja2 import Environment, FileSystemLoader

from factory.models import AgentVerdict, Observation, PerformanceReport

log = structlog.get_logger()


def _extract_exp_number(stem: str) -> str:
    """Extract trailing numeric experiment ID from a stem like 'myproject-042' or '042'."""
    match = re.search(r"(\d+)$", stem)
    return match.group(1) if match else stem


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
        seen_exp_nums: set[str] = set()
        archive_experiments = archive_dir / "experiments"
        json_source = sorted(archive_experiments.glob("**/*.json"))[:50] if archive_experiments.is_dir() else []
        for json_file in json_source:
            try:
                data = json.loads(json_file.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            content = data.get("learned", "") or data.get("ceo_rationale", "") or json.dumps(data)[:500]
            if len(content) > 10:
                exp_num = _extract_exp_number(json_file.stem)
                seen_exp_nums.add(exp_num)
                observations.append(Observation(
                    source=str(json_file.relative_to(project_path)),
                    content=content[:500],
                    timestamp=datetime.now(),
                    project=project_name,
                    tags=["archive"],
                ))
        md_source = sorted(archive_experiments.glob("**/*.md"))[:50] if archive_experiments.is_dir() else []
        for note_file in md_source:
            if _extract_exp_number(note_file.stem) in seen_exp_nums:
                continue
            try:
                text = note_file.read_text()
            except OSError:
                continue
            if len(text) > 50:
                observations.append(Observation(
                    source=str(note_file.relative_to(project_path)),
                    content=text[:500],
                    timestamp=datetime.now(),
                    project=project_name,
                    tags=["archive"],
                ))
        # Non-experiment archive notes (sources, patterns, etc.)
        for note_file in sorted(archive_dir.glob("**/*.md"))[:50]:
            if archive_experiments.is_dir() and note_file.is_relative_to(archive_experiments):
                continue
            try:
                text = note_file.read_text()
            except OSError:
                continue
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


def _read_goal(project_path: Path) -> str | None:
    """Read the goal field from .factory/config.json."""
    config_path = project_path / ".factory" / "config.json"
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text())
        goal = data.get("goal")
        return goal if isinstance(goal, str) and goal.strip() else None
    except (json.JSONDecodeError, OSError):
        return None


def _invoke_reporter(project_path: Path) -> str | None:
    """Invoke the reporter agent and return its output."""
    import subprocess

    goal = _read_goal(project_path)
    task = (
        f"Assess whether the project goal was achieved. "
        f"Project path: {project_path}\n"
        f"Goal: {goal or 'No goal set'}"
    )
    try:
        result = subprocess.run(
            [
                "factory", "agent", "reporter",
                "--task", task,
                "--project", str(project_path),
                "--model", "haiku",
                "--timeout", "120",
            ],
            capture_output=True, text=True, timeout=150,
        )
        if result.returncode != 0:
            log.warning("reporter_agent_failed", returncode=result.returncode)
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.warning("reporter_agent_error", error=str(exc))
        return None

    review_path = project_path / ".factory" / "reviews" / "reporter-latest.md"
    if review_path.exists():
        return review_path.read_text()
    return None


def _parse_goal_assessment(text: str) -> dict | None:
    """Parse a reporter agent assessment into a dict for the template."""
    overall_match = re.search(
        r"\*\*Overall:\*\*\s*(ACHIEVED|PARTIALLY_ACHIEVED|NOT_ACHIEVED|INSUFFICIENT_DATA)",
        text,
    )
    if not overall_match:
        return None

    overall = overall_match.group(1)
    overall_class_map = {
        "ACHIEVED": "keep",
        "PARTIALLY_ACHIEVED": "redirect",
        "NOT_ACHIEVED": "revert",
        "INSUFFICIENT_DATA": "insufficient",
    }
    verdict_class_map = {
        "MET": "keep",
        "PARTIALLY_MET": "redirect",
        "NOT_MET": "revert",
        "NO_DATA": "insufficient",
    }

    asks: list[dict] = []
    for ask_match in re.finditer(
        r"#### Ask:\s*(.+?)\n\*\*Verdict:\*\*\s*(MET|PARTIALLY_MET|NOT_MET|NO_DATA)\s*\n"
        r"\*\*Evidence:\*\*\s*\n(.*?)(?=\n#### Ask:|\n### Gaps|\n## |\Z)",
        text, re.DOTALL,
    ):
        ask_text = ask_match.group(1).strip()
        verdict = ask_match.group(2)
        evidence_lines: list[str] = []
        for line in ask_match.group(3).strip().splitlines():
            line = line.strip().lstrip("- ")
            if line:
                evidence_lines.append(line)
        asks.append({
            "ask": ask_text,
            "verdict": verdict,
            "verdict_class": verdict_class_map.get(verdict, ""),
            "evidence": evidence_lines,
        })

    gaps: list[str] = []
    gaps_match = re.search(r"### Gaps\s*\n(.+?)(?=\n### |\n## |\Z)", text, re.DOTALL)
    if gaps_match:
        for line in gaps_match.group(1).strip().splitlines():
            line = line.strip().lstrip("- ")
            if line and line.lower() != "none":
                gaps.append(line)

    return {
        "overall": overall,
        "overall_class": overall_class_map.get(overall, ""),
        "asks": asks,
        "gaps": gaps,
    }


def generate_html_report(
    project_path: Path,
    output_path: Path | None = None,
    *,
    assess: bool = False,
) -> Path:
    """Render a self-contained HTML report for a project."""
    from factory.store import ExperimentStore

    report = build_performance_report(project_path)

    store = ExperimentStore(project_path)
    try:
        experiments = asyncio.run(store.load_history())
    except Exception:
        experiments = []

    goal = _read_goal(project_path)

    goal_assessment: dict | None = None
    if assess:
        reporter_output = _invoke_reporter(project_path)
        if reporter_output:
            goal_assessment = _parse_goal_assessment(reporter_output)

    template_dir = Path(__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    template = env.get_template("report.html.j2")

    html = template.render(
        report=report,
        experiments=experiments,
        generated_at=report.generated_at.strftime("%Y-%m-%d %H:%M:%S"),
        goal=goal,
        goal_assessment=goal_assessment,
    )

    if output_path is None:
        output_path = project_path / ".factory" / "report.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)

    log.info("html_report_generated", path=str(output_path))
    return output_path


def build_performance_report(project_path: Path) -> PerformanceReport:
    """Build a complete performance report for a project."""
    from factory.store import ExperimentStore

    project_name = project_path.resolve().name
    store = ExperimentStore(project_path)

    try:
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

    for obs in data.get("observations", []):
        if "timestamp" in obs and isinstance(obs["timestamp"], str):
            obs["timestamp"] = datetime.fromisoformat(obs["timestamp"])
