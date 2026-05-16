"""Tests for enhanced reflector — performance report integration."""

import json
from datetime import datetime
from pathlib import Path

from factory.ace.reflector import (
    _load_from_reports,
    _observation_bullets,
    _verdict_bullets,
    reflect_on_experiments,
)
from factory.models import PerformanceReport


def _make_project_with_report(
    tmp_path: Path,
    name: str,
    report: PerformanceReport,
) -> Path:
    """Create a minimal project with a performance report and TSV."""
    import csv
    import io

    project = tmp_path / name
    factory_dir = project / ".factory"
    factory_dir.mkdir(parents=True)

    # Write performance report
    (factory_dir / "performance_report.json").write_text(
        json.dumps(report.model_dump(), indent=2, default=str) + "\n"
    )

    # Write minimal results.tsv
    columns = [
        "id", "timestamp", "hypothesis", "change_summary", "issue_number",
        "pr_number", "score_before", "score_after", "delta", "verdict",
        "cost_usd", "notes", "research_citations",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf, dialect="excel-tab")
    writer.writerow(columns)
    for i in range(report.total_experiments):
        verdict = "keep" if i < report.keep_count else "revert"
        writer.writerow([
            i + 1, "2026-01-01T00:00:00",
            f"Hypothesis {i + 1}", f"Change {i + 1}",
            "", "", "0.7", "0.8" if verdict == "keep" else "0.65",
            "0.1" if verdict == "keep" else "-0.05", verdict,
            "", "", "",
        ])
    (factory_dir / "results.tsv").write_text(buf.getvalue())

    return project


def test_verdict_bullets_with_redirects(tmp_path: Path) -> None:
    report = PerformanceReport(
        project_name="proj1",
        generated_at=datetime.now(),
        total_experiments=5,
        keep_count=3,
        revert_count=2,
        error_count=0,
        keep_rate=0.6,
        verdict_patterns={
            "builder:REDIRECT": 4,
            "researcher:PROCEED": 3,
        },
    )

    project = _make_project_with_report(tmp_path, "proj1", report)
    bullets = _verdict_bullets([project])

    assert len(bullets) >= 1
    assert any("builder" in b.content.lower() for b in bullets)
    assert any("REDIRECT" in b.content for b in bullets)


def test_verdict_bullets_with_aborts(tmp_path: Path) -> None:
    report = PerformanceReport(
        project_name="proj1",
        generated_at=datetime.now(),
        total_experiments=3,
        keep_count=1,
        revert_count=1,
        error_count=1,
        keep_rate=0.33,
        verdict_patterns={"builder:ABORT": 2, "researcher:ABORT": 1},
    )

    project = _make_project_with_report(tmp_path, "proj1", report)
    bullets = _verdict_bullets([project])

    assert any("ABORT" in b.content for b in bullets)


def test_verdict_bullets_no_issues(tmp_path: Path) -> None:
    report = PerformanceReport(
        project_name="proj1",
        generated_at=datetime.now(),
        total_experiments=3,
        keep_count=3,
        revert_count=0,
        error_count=0,
        keep_rate=1.0,
        verdict_patterns={"researcher:PROCEED": 3, "builder:PROCEED": 3},
    )

    project = _make_project_with_report(tmp_path, "proj1", report)
    bullets = _verdict_bullets([project])
    assert bullets == []


def test_observation_bullets_low_archive(tmp_path: Path) -> None:
    from factory.models import Observation

    observations = [
        Observation(
            source="observations.md", content=f"Obs {i}",
            timestamp=datetime.now(), project="proj1", tags=["observation"],
        )
        for i in range(10)
    ] + [
        Observation(
            source=".factory/archive/note.md", content="Archive obs",
            timestamp=datetime.now(), project="proj1", tags=["archive"],
        )
    ]

    report = PerformanceReport(
        project_name="proj1",
        generated_at=datetime.now(),
        total_experiments=5,
        keep_count=3,
        revert_count=2,
        error_count=0,
        keep_rate=0.6,
        observations=observations,
    )

    project = _make_project_with_report(tmp_path, "proj1", report)
    bullets = _observation_bullets([project])
    assert len(bullets) >= 1
    assert any("archive coverage" in b.content.lower() for b in bullets)


def test_load_from_reports_with_report(tmp_path: Path) -> None:
    report = PerformanceReport(
        project_name="proj1",
        generated_at=datetime.now(),
        total_experiments=2,
        keep_count=1,
        revert_count=1,
        error_count=0,
        keep_rate=0.5,
    )

    project = _make_project_with_report(tmp_path, "proj1", report)
    all_records, outcomes, histories = _load_from_reports([project])

    assert len(all_records) == 2
    assert len(outcomes) == 2
    assert "proj1" in histories


def test_load_from_reports_tsv_fallback(tmp_path: Path) -> None:
    """When no performance report exists, falls back to TSV loading."""
    import csv
    import io

    project = tmp_path / "proj-no-report"
    factory_dir = project / ".factory"
    factory_dir.mkdir(parents=True)

    columns = [
        "id", "timestamp", "hypothesis", "change_summary", "issue_number",
        "pr_number", "score_before", "score_after", "delta", "verdict",
        "cost_usd", "notes", "research_citations",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf, dialect="excel-tab")
    writer.writerow(columns)
    writer.writerow([
        1, "2026-01-01T00:00:00", "Test hypothesis", "change",
        "", "", "0.7", "0.8", "0.1", "keep", "", "", "",
    ])
    (factory_dir / "results.tsv").write_text(buf.getvalue())

    all_records, outcomes, histories = _load_from_reports([project])

    assert len(all_records) == 1
    assert len(outcomes) == 1


def test_reflect_uses_registry_fallback(tmp_path: Path) -> None:
    """reflect_on_experiments should fall back to discover_projects when registry is empty."""
    import csv
    import io

    projects_dir = tmp_path / "projects"
    project = projects_dir / "proj1"
    factory_dir = project / ".factory"
    factory_dir.mkdir(parents=True)

    columns = [
        "id", "timestamp", "hypothesis", "change_summary", "issue_number",
        "pr_number", "score_before", "score_after", "delta", "verdict",
        "cost_usd", "notes", "research_citations",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf, dialect="excel-tab")
    writer.writerow(columns)
    for i in range(6):
        writer.writerow([
            i + 1, "2026-01-01T00:00:00",
            "Add tests for coverage", "Added tests",
            "", "", "0.7", "0.8", "0.1", "keep", "", "", "",
        ])
    (factory_dir / "results.tsv").write_text(buf.getvalue())

    candidates = reflect_on_experiments(projects_dir)
    assert isinstance(candidates, dict)
