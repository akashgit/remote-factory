"""Tests for factory.report — performance reports."""

from pathlib import Path

from factory.report import (
    build_performance_report,
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


# ── _extract_exp_number ──────────────────────────────────────────


def test_extract_exp_number_with_prefix() -> None:
    from factory.report import _extract_exp_number

    assert _extract_exp_number("myproject-042") == "042"


def test_extract_exp_number_digits_only() -> None:
    from factory.report import _extract_exp_number

    assert _extract_exp_number("042") == "042"


def test_extract_exp_number_no_digits() -> None:
    from factory.report import _extract_exp_number

    assert _extract_exp_number("no-number-here") == "no-number-here"


# ── parse_ceo_verdicts — experiment ID and no-verdict skip ───────


def test_parse_ceo_verdicts_with_experiment_id(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    (factory_dir / "reviews" / "ceo-verdict-qa.md").write_text(
        "## CEO Review: QA Agent\n"
        "Results from experiment 3\n"
        "- **Verdict:** ABORT\n"
        "- **Rationale:** Critical failure\n"
    )

    verdicts = parse_ceo_verdicts(project)
    assert len(verdicts) == 1
    assert verdicts[0].experiment_id == 3
    assert verdicts[0].verdict == "ABORT"


def test_parse_ceo_verdicts_no_verdict_match(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    (factory_dir / "reviews" / "ceo-verdict-builder.md").write_text(
        "## CEO Review: Builder Agent\n"
        "No structured verdict here, just free text.\n"
    )

    verdicts = parse_ceo_verdicts(project)
    assert verdicts == []


# ── parse_observations — archive JSON files ─────────────────────


def test_parse_observations_archive_json_valid(tmp_path: Path) -> None:
    """Valid JSON dict with 'learned' key in archive/experiments/."""
    import json

    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    archive_exp = factory_dir / "archive" / "experiments"
    archive_exp.mkdir(parents=True)

    (archive_exp / "proj-001.json").write_text(
        json.dumps({"learned": "We discovered that caching improves throughput significantly."})
    )

    observations = parse_observations(project)
    assert any("caching" in o.content for o in observations)
    assert any("archive" in o.tags for o in observations)


def test_parse_observations_archive_json_invalid(tmp_path: Path) -> None:
    """Invalid JSON in archive/experiments/ should be skipped."""
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    archive_exp = factory_dir / "archive" / "experiments"
    archive_exp.mkdir(parents=True)

    (archive_exp / "bad.json").write_text("not valid json {{{")

    observations = parse_observations(project)
    json_obs = [o for o in observations if "bad.json" in o.source]
    assert json_obs == []


def test_parse_observations_archive_json_non_dict(tmp_path: Path) -> None:
    """JSON that parses to a non-dict (e.g. a list) should be skipped."""
    import json

    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    archive_exp = factory_dir / "archive" / "experiments"
    archive_exp.mkdir(parents=True)

    (archive_exp / "list.json").write_text(json.dumps([1, 2, 3]))

    observations = parse_observations(project)
    json_obs = [o for o in observations if "list.json" in o.source]
    assert json_obs == []


def test_parse_observations_archive_md_skipped_by_exp_number(tmp_path: Path) -> None:
    """An .md file whose exp number overlaps with a seen JSON exp number should be skipped."""
    import json

    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    archive_exp = factory_dir / "archive" / "experiments"
    archive_exp.mkdir(parents=True)

    # JSON for experiment 007 — will be seen first
    (archive_exp / "proj-007.json").write_text(
        json.dumps({"learned": "JSON observation that is long enough to pass the 10-char threshold."})
    )
    # MD for same experiment number — should be skipped
    (archive_exp / "proj-007.md").write_text(
        "This is a markdown note for the same experiment that should be skipped because JSON was already seen."
    )

    observations = parse_observations(project)
    md_obs = [o for o in observations if o.source.endswith("proj-007.md")]
    assert md_obs == []
    json_obs = [o for o in observations if o.source.endswith("proj-007.json")]
    assert len(json_obs) == 1


def test_parse_observations_archive_md_short_content(tmp_path: Path) -> None:
    """An .md file with content shorter than 50 chars should be skipped."""
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    archive_exp = factory_dir / "archive" / "experiments"
    archive_exp.mkdir(parents=True)

    (archive_exp / "short.md").write_text("Too short.")

    observations = parse_observations(project)
    short_obs = [o for o in observations if "short.md" in o.source]
    assert short_obs == []


def test_parse_observations_non_experiment_archive_skip_experiment_subdir(tmp_path: Path) -> None:
    """Non-experiment archive .md files that ARE under archive/experiments/ should be skipped
    in the final loop (line 134)."""

    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    archive_dir = factory_dir / "archive"
    archive_exp = archive_dir / "experiments"
    archive_exp.mkdir(parents=True)

    # A patterns dir outside experiments — should be picked up
    patterns_dir = archive_dir / "patterns"
    patterns_dir.mkdir()
    (patterns_dir / "pattern1.md").write_text(
        "This is a pattern note that is long enough to exceed the 50-char threshold for inclusion."
    )

    observations = parse_observations(project)
    pattern_obs = [o for o in observations if "pattern1.md" in o.source]
    assert len(pattern_obs) == 1


def test_parse_observations_non_experiment_archive_short_md(tmp_path: Path) -> None:
    """Non-experiment archive .md files shorter than 50 chars should be skipped (line 139)."""
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    archive_dir = factory_dir / "archive"
    archive_dir.mkdir(parents=True)

    patterns_dir = archive_dir / "patterns"
    patterns_dir.mkdir()
    (patterns_dir / "tiny.md").write_text("Short.")

    observations = parse_observations(project)
    tiny_obs = [o for o in observations if "tiny.md" in o.source]
    assert tiny_obs == []


# ── _parse_datetimes ─────────────────────────────────────────────


def test_parse_datetimes_converts_iso_strings() -> None:
    from datetime import datetime

    from factory.report import _parse_datetimes

    data: dict = {
        "generated_at": "2026-01-15T10:30:00",
        "observations": [
            {"timestamp": "2026-01-14T08:00:00", "other": "value"},
            {"timestamp": "2026-01-13T09:00:00"},
        ],
    }
    _parse_datetimes(data)

    assert isinstance(data["generated_at"], datetime)
    assert data["generated_at"].year == 2026
    assert data["generated_at"].month == 1
    assert data["generated_at"].day == 15

    for obs in data["observations"]:
        assert isinstance(obs["timestamp"], datetime)


def test_parse_datetimes_skips_non_string_values() -> None:
    from datetime import datetime

    from factory.report import _parse_datetimes

    now = datetime.now()
    data: dict = {
        "generated_at": now,
        "observations": [{"timestamp": now}],
    }
    _parse_datetimes(data)

    # Should remain unchanged
    assert data["generated_at"] is now
    assert data["observations"][0]["timestamp"] is now


# ── build_performance_report — store.load_history() exception ────


def test_build_performance_report_history_exception(tmp_path: Path) -> None:
    """When store.load_history() raises, records should default to []."""
    from unittest.mock import AsyncMock, patch

    project = tmp_path / "proj"
    _make_factory_dir(project)

    mock_store = AsyncMock()
    mock_store.load_history.side_effect = RuntimeError("DB gone")

    with patch("factory.store.ExperimentStore", return_value=mock_store):
        report = build_performance_report(project)

    assert report.total_experiments == 0
    assert report.keep_count == 0
    assert report.revert_count == 0
    assert report.error_count == 0
    assert report.latest_score is None


def test_parse_observations_section_no_content(tmp_path: Path) -> None:
    """Observation sections with title only (no content) should be skipped (line 83)."""
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    (factory_dir / "strategy" / "observations.md").write_text(
        "## Empty Section\n\n## Also Empty\n"
    )

    observations = parse_observations(project)
    assert observations == []


def test_parse_ceo_verdicts_issues_with_empty_line(tmp_path: Path) -> None:
    """Issues block with a blank line between items — blank line should be skipped (line 50)."""
    project = tmp_path / "proj"
    factory_dir = _make_factory_dir(project)

    (factory_dir / "reviews" / "ceo-verdict-qa.md").write_text(
        "- **Verdict:** REDIRECT\n"
        "- **Rationale:** Needs work\n"
        "- **Issues found:**\n"
        "- First issue\n"
        "\n"
        "- Second issue\n"
    )

    verdicts = parse_ceo_verdicts(project)
    assert len(verdicts) == 1
    assert len(verdicts[0].issues) == 2
