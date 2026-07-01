"""Tests for factory.report — performance reports."""

from pathlib import Path

from factory.report import (
    build_performance_report,
    generate_html_report,
    load_performance_report,
    parse_ceo_verdicts,
    parse_observations,
    save_performance_report,
)


def _make_factory_dir(project: Path) -> Path:
    factory_dir = project / ".factory"
    factory_dir.mkdir(parents=True, exist_ok=True)
    (factory_dir / "experiments").mkdir(exist_ok=True)
    (factory_dir / "reviews").mkdir(exist_ok=True)
    (factory_dir / "strategy").mkdir(exist_ok=True)
    return factory_dir


def _write_results_tsv(factory_dir: Path, rows: list[dict]) -> None:
    import csv
    import io

    columns = [
        "id", "timestamp", "hypothesis", "change_summary", "issue_number",
        "pr_number", "score_before", "score_after", "delta", "verdict",
        "cost_usd", "notes", "research_citations",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf, dialect="excel-tab")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(c, "") for c in columns])
    (factory_dir / "results.tsv").write_text(buf.getvalue())


def test_parse_ceo_verdicts(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    (factory_dir / "reviews" / "ceo-verdict-researcher.md").write_text(
        "## CEO Review: Researcher Agent\n"
        "- **Verdict:** PROCEED\n"
        "- **Rationale:** Good research coverage\n"
        "- **Issues found:** none\n"
    )
    (factory_dir / "reviews" / "ceo-verdict-builder.md").write_text(
        "## CEO Review: Builder Agent\n"
        "- **Verdict:** REDIRECT\n"
        "- **Rationale:** Missing tests for experiment 3\n"
        "- **Issues found:**\n"
        "- No unit tests\n"
        "- Missing error handling\n"
    )

    verdicts = parse_ceo_verdicts(project)
    assert len(verdicts) == 2

    researcher_v = next(v for v in verdicts if v.role == "researcher")
    assert researcher_v.verdict == "PROCEED"
    assert "coverage" in researcher_v.rationale.lower()

    builder_v = next(v for v in verdicts if v.role == "builder")
    assert builder_v.verdict == "REDIRECT"
    assert len(builder_v.issues) == 2


def test_parse_ceo_verdicts_empty(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    verdicts = parse_ceo_verdicts(project)
    assert verdicts == []


def test_parse_observations(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    (factory_dir / "strategy" / "observations.md").write_text(
        "## Code Quality\nThe code has good test coverage.\n\n"
        "## Performance\nSlow startup time observed.\n"
    )

    observations = parse_observations(project)
    assert len(observations) >= 2
    assert any("Code Quality" in o.content for o in observations)


def test_parse_observations_with_archive(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    archive_dir = factory_dir / "archive"
    archive_dir.mkdir()
    (archive_dir / "note1.md").write_text(
        "# Experiment Note\nSome learning from experiment 1 that is long enough to be included."
    )

    observations = parse_observations(project)
    assert any("archive" in o.tags for o in observations)


def test_build_performance_report(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    _write_results_tsv(factory_dir, [
        {
            "id": "1", "timestamp": "2026-01-01T00:00:00",
            "hypothesis": "Add tests", "change_summary": "Added unit tests",
            "verdict": "keep", "score_before": "0.7", "score_after": "0.8",
            "delta": "0.1",
        },
        {
            "id": "2", "timestamp": "2026-01-02T00:00:00",
            "hypothesis": "Fix lint", "change_summary": "Fixed linting",
            "verdict": "revert", "score_before": "0.8", "score_after": "0.75",
            "delta": "-0.05",
        },
    ])

    report = build_performance_report(project)
    assert report.project_name == "proj"
    assert report.total_experiments == 2
    assert report.keep_count == 1
    assert report.revert_count == 1
    assert report.keep_rate == 0.5
    assert report.latest_score == 0.75


def test_save_and_load_performance_report(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)
    _write_results_tsv(factory_dir, [])

    path = save_performance_report(project)
    assert path.exists()

    loaded = load_performance_report(project)
    assert loaded is not None
    assert loaded.project_name == "proj"
    assert loaded.total_experiments == 0


def test_load_performance_report_missing(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    loaded = load_performance_report(project)
    assert loaded is None


def test_load_performance_report_corrupt(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)
    (factory_dir / "performance_report.json").write_text("not json")
    loaded = load_performance_report(project)
    assert loaded is None


def test_verdict_patterns_in_report(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    _write_results_tsv(factory_dir, [])

    (factory_dir / "reviews" / "ceo-verdict-researcher.md").write_text(
        "- **Verdict:** PROCEED\n- **Rationale:** ok\n"
    )
    (factory_dir / "reviews" / "ceo-verdict-builder.md").write_text(
        "- **Verdict:** REDIRECT\n- **Rationale:** bad\n"
    )

    report = build_performance_report(project)
    assert "researcher:PROCEED" in report.verdict_patterns
    assert "builder:REDIRECT" in report.verdict_patterns


def test_generate_html_report_creates_file(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)
    _write_results_tsv(factory_dir, [
        {
            "id": "1", "timestamp": "2026-01-01T00:00:00",
            "hypothesis": "Add tests", "change_summary": "Added unit tests",
            "verdict": "keep", "score_before": "0.7", "score_after": "0.8",
            "delta": "0.1",
        },
    ])

    result = generate_html_report(project)
    assert result.exists()
    assert result == project / ".factory" / "report.html"


def test_generate_html_report_contains_structure(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)
    _write_results_tsv(factory_dir, [
        {
            "id": "1", "timestamp": "2026-01-01T00:00:00",
            "hypothesis": "Add tests", "change_summary": "Added unit tests",
            "verdict": "keep", "score_before": "0.7", "score_after": "0.8",
            "delta": "0.1",
        },
    ])
    (factory_dir / "reviews" / "ceo-verdict-researcher.md").write_text(
        "- **Verdict:** PROCEED\n- **Rationale:** Good coverage\n"
    )

    result = generate_html_report(project)
    html = result.read_text()

    assert "<table>" in html
    assert "Add tests" in html
    assert "keep" in html.lower()
    assert "0.70" in html
    assert "0.80" in html
    assert "+0.10" in html
    assert "PROCEED" in html
    assert "researcher" in html
    assert "Summary" in html
    assert "Experiments" in html
    assert "Agent Verdicts" in html


def test_generate_html_report_empty_project(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    _make_factory_dir(project)
    _write_results_tsv(project / ".factory", [])

    result = generate_html_report(project)
    assert result.exists()
    html = result.read_text()
    assert "No experiments recorded." in html
    assert "No agent verdicts recorded." in html


def test_generate_html_report_custom_output(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    _make_factory_dir(project)
    _write_results_tsv(project / ".factory", [])

    custom = tmp_path / "custom" / "report.html"
    result = generate_html_report(project, output_path=custom)
    assert result == custom
    assert custom.exists()
