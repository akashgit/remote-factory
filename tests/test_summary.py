"""Tests for factory.summary — session summary generation, formatting, and persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from factory.models import ExperimentRecord, SessionSummary
from factory.summary import (
    format_summary,
    generate_summary,
    save_summary,
)


# ── fixtures ─────────────────────────────────────────────────


def _make_record(
    *,
    id: int = 1,
    verdict: str = "keep",
    hypothesis: str = "Add structured logging",
    delta: float | None = 0.045,
    score_before: float | None = 0.70,
    score_after: float | None = 0.745,
    pr_number: int | None = 42,
    cost_usd: float | None = 1.50,
) -> ExperimentRecord:
    return ExperimentRecord(
        id=id,
        timestamp=datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc),
        hypothesis=hypothesis,
        change_summary="Added logging",
        issue_number=None,
        pr_number=pr_number,
        score_before=score_before,
        score_after=score_after,
        delta=delta,
        verdict=verdict,
        cost_usd=cost_usd,
        notes="",
    )


@pytest.fixture
def summary_project(tmp_path: Path) -> Path:
    """Create a project with .factory/ structure and populated results."""
    project = tmp_path / "summary-project"
    project.mkdir()
    factory = project / ".factory"
    factory.mkdir()
    (factory / "experiments").mkdir()
    (factory / "strategy").mkdir()
    (factory / "reviews").mkdir()

    # Write results.tsv with mixed verdicts
    import csv
    import io

    from factory.store import TSV_COLUMNS

    buf = io.StringIO()
    writer = csv.writer(buf, dialect="excel-tab")
    writer.writerow(TSV_COLUMNS)
    for row in [
        [1, "2026-04-26T10:00:00+00:00", "Add logging", "Added logging", "", "42",
         "0.700", "0.745", "0.045", "keep", "1.50", "", ""],
        [2, "2026-04-26T11:00:00+00:00", "Fix imports", "Fixed imports", "", "43",
         "0.745", "0.760", "0.015", "keep", "0.80", "", ""],
        [3, "2026-04-26T12:00:00+00:00", "Add caching", "Added caching", "", "",
         "0.760", "0.755", "-0.005", "revert", "2.00", "", ""],
        [4, "2026-04-26T13:00:00+00:00", "Broken refactor", "Refactored", "", "",
         "0.760", "", "", "error", "0.50", "", ""],
    ]:
        writer.writerow(row)
    (factory / "results.tsv").write_text(buf.getvalue())

    # Write backlog
    (factory / "strategy" / "backlog.md").write_text(
        "- Add rate limiting\n"
        "- Improve test coverage\n"
        "- Write API docs\n"
    )

    # Write eval_after with guard violations for experiment 3
    exp3 = factory / "experiments" / "003"
    exp3.mkdir()
    (exp3 / "eval_after.json").write_text(json.dumps({
        "total": 0.755,
        "results": [],
        "guard_violations": ["scope_check: modified files outside scope"],
        "passed": False,
    }))

    # Write events.jsonl with mode info
    (factory / "events.jsonl").write_text(
        json.dumps({"type": "cycle.started", "timestamp": "2026-04-26T09:00:00Z",
                     "project": "summary-project", "agent": None,
                     "data": {"cycle": 1, "mode": "improve"}}) + "\n"
    )

    return project


# ── generate_summary tests ───────────────────────────────────


async def test_generate_empty_history(tmp_path: Path) -> None:
    """Empty project yields empty summary lists."""
    project = tmp_path / "empty-project"
    project.mkdir()
    factory = project / ".factory"
    factory.mkdir()
    (factory / "experiments").mkdir()
    (factory / "strategy").mkdir()
    (factory / "reviews").mkdir()

    # Empty results.tsv (header only)
    import csv
    import io

    from factory.store import TSV_COLUMNS

    buf = io.StringIO()
    csv.writer(buf, dialect="excel-tab").writerow(TSV_COLUMNS)
    (factory / "results.tsv").write_text(buf.getvalue())

    summary = await generate_summary(project)
    assert summary.experiments_kept == []
    assert summary.experiments_reverted == []
    assert summary.experiments_errored == []
    assert summary.score_start is None
    assert summary.score_end is None
    assert summary.total_cost_usd is None


async def test_generate_mixed_verdicts(summary_project: Path) -> None:
    """Experiments are correctly bucketed by verdict."""
    summary = await generate_summary(summary_project)
    assert len(summary.experiments_kept) == 2
    assert len(summary.experiments_reverted) == 1
    assert len(summary.experiments_errored) == 1
    assert summary.experiments_kept[0].id == 1
    assert summary.experiments_kept[1].id == 2
    assert summary.experiments_reverted[0].id == 3
    assert summary.experiments_errored[0].id == 4


async def test_backlog_included(summary_project: Path) -> None:
    """Backlog items from backlog.md appear in summary."""
    summary = await generate_summary(summary_project)
    assert "Add rate limiting" in summary.backlog_remaining
    assert "Improve test coverage" in summary.backlog_remaining
    assert "Write API docs" in summary.backlog_remaining


async def test_guard_violations_collected(summary_project: Path) -> None:
    """Guard violations from eval_after.json are collected."""
    summary = await generate_summary(summary_project)
    assert len(summary.guard_violations) == 1
    assert "scope_check" in summary.guard_violations[0]


async def test_score_trajectory(summary_project: Path) -> None:
    """score_start and score_end come from first/last experiments."""
    summary = await generate_summary(summary_project)
    assert summary.score_start == 0.70
    # Last experiment (error) has no score_after
    assert summary.score_end is None


async def test_total_cost(summary_project: Path) -> None:
    """total_cost_usd sums across all experiments."""
    summary = await generate_summary(summary_project)
    assert summary.total_cost_usd == pytest.approx(4.80)


async def test_needs_human_input(summary_project: Path) -> None:
    """Errored experiments, guard violations, and marginal reverts appear."""
    summary = await generate_summary(summary_project)
    error_items = [i for i in summary.needs_human_input if "ERROR" in i]
    assert len(error_items) == 1
    assert "#4" in error_items[0]

    guard_items = [i for i in summary.needs_human_input if "Guard violation" in i]
    assert len(guard_items) == 1

    marginal_items = [i for i in summary.needs_human_input if "MARGINAL" in i]
    assert len(marginal_items) == 1
    assert "#3" in marginal_items[0]


async def test_mode_from_events(summary_project: Path) -> None:
    """Mode is detected from events.jsonl."""
    summary = await generate_summary(summary_project)
    assert summary.mode == "improve"


# ── format_summary tests ────────────────────────────────────


def test_format_output() -> None:
    """All sections present in formatted markdown."""
    summary = SessionSummary(
        project_name="test-project",
        generated_at=datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc),
        mode="improve",
        experiments_kept=[_make_record(id=1, verdict="keep")],
        experiments_reverted=[_make_record(id=2, verdict="revert", delta=-0.005)],
        experiments_errored=[],
        backlog_remaining=["Add caching", "Write docs"],
        guard_violations=[],
        needs_human_input=["Experiment #2 [MARGINAL REVERT]: test"],
        score_start=0.70,
        score_end=0.745,
        total_cost_usd=2.30,
    )
    output = format_summary(summary)
    assert "# Session Summary" in output
    assert "## Overview" in output
    assert "## What Was Built" in output
    assert "## What Was Deferred" in output
    assert "## Needs Your Input" in output
    assert "test-project" in output
    assert "improve" in output
    assert "0.7000" in output
    assert "0.7450" in output
    assert "$2.30" in output
    assert "Add caching" in output
    assert "#42" in output


def test_format_empty_kept() -> None:
    """No-keeps shows appropriate message."""
    summary = SessionSummary(
        project_name="test",
        generated_at=datetime(2026, 4, 26, tzinfo=timezone.utc),
        mode="improve",
        experiments_kept=[],
        experiments_reverted=[],
        experiments_errored=[],
        backlog_remaining=[],
        guard_violations=[],
        needs_human_input=[],
        score_start=None,
        score_end=None,
        total_cost_usd=None,
    )
    output = format_summary(summary)
    assert "No experiments were kept" in output
    assert "No items in backlog" in output
    assert "Nothing requires your attention" in output


def test_format_no_scores() -> None:
    """Score line omitted when scores are None."""
    summary = SessionSummary(
        project_name="test",
        generated_at=datetime(2026, 4, 26, tzinfo=timezone.utc),
        mode="build",
        experiments_kept=[],
        experiments_reverted=[],
        experiments_errored=[],
        backlog_remaining=[],
        guard_violations=[],
        needs_human_input=[],
        score_start=None,
        score_end=None,
        total_cost_usd=None,
    )
    output = format_summary(summary)
    assert "Score:" not in output


# ── save_summary tests ──────────────────────────────────────


async def test_save_creates_files(tmp_path: Path) -> None:
    """save_summary writes both .md and .json files."""
    project = tmp_path / "save-project"
    project.mkdir()

    summary = SessionSummary(
        project_name="save-project",
        generated_at=datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc),
        mode="improve",
        experiments_kept=[_make_record()],
        experiments_reverted=[],
        experiments_errored=[],
        backlog_remaining=["Item A"],
        guard_violations=[],
        needs_human_input=[],
        score_start=0.70,
        score_end=0.745,
        total_cost_usd=1.50,
    )

    md_path = await save_summary(project, summary)
    assert md_path.exists()
    assert md_path.name == "session-summary.md"
    assert "Session Summary" in md_path.read_text()

    json_path = project / ".factory" / "reviews" / "session-summary.json"
    assert json_path.exists()
    data = json.loads(json_path.read_text())
    assert data["project_name"] == "save-project"
    assert len(data["experiments_kept"]) == 1


async def test_save_creates_reviews_dir(tmp_path: Path) -> None:
    """save_summary creates .factory/reviews/ if missing."""
    project = tmp_path / "no-reviews"
    project.mkdir()

    summary = SessionSummary(
        project_name="no-reviews",
        generated_at=datetime(2026, 4, 26, tzinfo=timezone.utc),
        mode="build",
        experiments_kept=[],
        experiments_reverted=[],
        experiments_errored=[],
        backlog_remaining=[],
        guard_violations=[],
        needs_human_input=[],
        score_start=None,
        score_end=None,
        total_cost_usd=None,
    )

    md_path = await save_summary(project, summary)
    assert md_path.exists()


# ── CLI integration ──────────────────────────────────────────


def test_cli_summary(summary_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """factory summary <path> prints output and returns 0."""
    from factory.cli import main

    code = main(["summary", str(summary_project)])
    assert code == 0
    output = capsys.readouterr().out
    assert "Session Summary" in output
    assert "What Was Built" in output
    assert "What Was Deferred" in output
    assert "Needs Your Input" in output
    assert (summary_project / ".factory" / "reviews" / "session-summary.md").exists()
    assert (summary_project / ".factory" / "reviews" / "session-summary.json").exists()


# ── model tests ──────────────────────────────────────────────


def test_session_summary_strict() -> None:
    """SessionSummary rejects extra fields."""
    with pytest.raises(Exception):
        SessionSummary(
            project_name="test",
            generated_at=datetime(2026, 4, 26, tzinfo=timezone.utc),
            mode="improve",
            experiments_kept=[],
            experiments_reverted=[],
            experiments_errored=[],
            backlog_remaining=[],
            guard_violations=[],
            needs_human_input=[],
            score_start=None,
            score_end=None,
            total_cost_usd=None,
            extra_field="bad",
        )
