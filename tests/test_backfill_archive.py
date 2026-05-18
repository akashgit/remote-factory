"""Tests for factory backfill-archive command."""

import json
from pathlib import Path

import pytest

from factory.backfill_archive import backfill_archive, _generate_note
from factory.cli import main


@pytest.fixture
def factory_project(tmp_path: Path) -> Path:
    """Create a minimal factory project with experiments."""
    project = tmp_path / "my-project"
    project.mkdir()
    factory_dir = project / ".factory"
    factory_dir.mkdir()
    experiments_dir = factory_dir / "experiments"
    experiments_dir.mkdir()

    config = {
        "goal": "test",
        "scope": [],
        "guards": [],
        "eval_command": "echo ok",
        "eval_threshold": 0.5,
        "constraints": [],
    }
    (factory_dir / "config.json").write_text(json.dumps(config))

    # Write TSV header
    (factory_dir / "results.tsv").write_text(
        "id\ttimestamp\thypothesis\tchange_summary\tissue_number\t"
        "pr_number\tscore_before\tscore_after\tdelta\tverdict\t"
        "cost_usd\tnotes\tresearch_citations\n"
    )

    return project


def _add_experiment(
    project: Path,
    exp_id: int,
    hypothesis: str = "Test hypothesis",
    verdict: str = "keep",
    score_before: float = 0.5,
    score_after: float = 0.7,
) -> None:
    """Add an experiment directory with artifacts and a TSV row."""
    exp_dir = project / ".factory" / "experiments" / f"{exp_id:03d}"
    exp_dir.mkdir(parents=True, exist_ok=True)

    (exp_dir / "hypothesis.md").write_text(hypothesis)
    (exp_dir / "eval_before.json").write_text(json.dumps({
        "total": score_before,
        "results": [{"name": "tests", "score": score_before, "weight": 1.0, "passed": True, "details": "ok"}],
        "guard_violations": [],
        "passed": True,
    }))
    (exp_dir / "eval_after.json").write_text(json.dumps({
        "total": score_after,
        "results": [{"name": "tests", "score": score_after, "weight": 1.0, "passed": True, "details": "ok"}],
        "guard_violations": [],
        "passed": True,
    }))
    (exp_dir / "verdict.json").write_text(json.dumps({
        "id": exp_id,
        "timestamp": "2026-01-01T00:00:00",
        "hypothesis": hypothesis,
        "change_summary": "Changed some files",
        "issue_number": None,
        "pr_number": None,
        "score_before": score_before,
        "score_after": score_after,
        "delta": round(score_after - score_before, 6),
        "verdict": verdict,
        "cost_usd": None,
        "notes": "All tests pass",
    }))
    (exp_dir / "changes.diff").write_text("diff --git a/foo.py b/foo.py\n+print('hello')\n")

    delta = round(score_after - score_before, 6)
    row = f"{exp_id}\t2026-01-01T00:00:00\t{hypothesis}\tChanged some files\t\t\t{score_before}\t{score_after}\t{delta}\t{verdict}\t\tAll tests pass\t\n"
    tsv_path = project / ".factory" / "results.tsv"
    with open(tsv_path, "a") as f:
        f.write(row)


class TestBackfillArchive:
    async def test_no_experiments_dir(self, tmp_path: Path) -> None:
        project = tmp_path / "empty-project"
        project.mkdir()
        (project / ".factory").mkdir()
        result = await backfill_archive(project)
        assert result == {"existed": 0, "created": 0, "total": 0}

    async def test_creates_notes_for_all_experiments(self, factory_project: Path) -> None:
        _add_experiment(factory_project, 1, hypothesis="First experiment")
        _add_experiment(factory_project, 2, hypothesis="Second experiment")

        result = await backfill_archive(factory_project)

        assert result["existed"] == 0
        assert result["created"] == 2
        assert result["total"] == 2

        archive_dir = factory_project / ".factory" / "archive" / "experiments"
        assert (archive_dir / "my-project-001.md").exists()
        assert (archive_dir / "my-project-002.md").exists()

    async def test_does_not_overwrite_existing(self, factory_project: Path) -> None:
        _add_experiment(factory_project, 1)

        archive_dir = factory_project / ".factory" / "archive" / "experiments"
        archive_dir.mkdir(parents=True, exist_ok=True)
        existing_note = archive_dir / "my-project-001.md"
        existing_note.write_text("Hand-written note — do not overwrite")

        result = await backfill_archive(factory_project)

        assert result["existed"] == 1
        assert result["created"] == 0
        assert existing_note.read_text() == "Hand-written note — do not overwrite"

    async def test_mixed_existing_and_new(self, factory_project: Path) -> None:
        _add_experiment(factory_project, 1)
        _add_experiment(factory_project, 2)
        _add_experiment(factory_project, 3)

        archive_dir = factory_project / ".factory" / "archive" / "experiments"
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "my-project-002.md").write_text("Existing note")

        result = await backfill_archive(factory_project)

        assert result["existed"] == 1
        assert result["created"] == 2
        assert result["total"] == 3

    async def test_note_content_structure(self, factory_project: Path) -> None:
        _add_experiment(factory_project, 1, hypothesis="Improve error handling", verdict="keep")

        await backfill_archive(factory_project)

        archive_dir = factory_project / ".factory" / "archive" / "experiments"
        content = (archive_dir / "my-project-001.md").read_text()

        assert "# Experiment 001" in content
        assert "Improve error handling" in content
        assert "**Verdict:** keep" in content
        assert "## Hypothesis" in content
        assert "## Eval Delta" in content
        assert "## Decision Rationale" in content
        assert "## What Changed" in content
        assert "## Changes (diff)" in content

    async def test_idempotent(self, factory_project: Path) -> None:
        _add_experiment(factory_project, 1)

        result1 = await backfill_archive(factory_project)
        assert result1["created"] == 1

        result2 = await backfill_archive(factory_project)
        assert result2["created"] == 0
        assert result2["existed"] == 1

    async def test_experiment_without_all_artifacts(self, factory_project: Path) -> None:
        exp_dir = factory_project / ".factory" / "experiments" / "001"
        exp_dir.mkdir(parents=True)
        (exp_dir / "hypothesis.md").write_text("Partial experiment")

        result = await backfill_archive(factory_project)

        assert result["created"] == 1
        archive_dir = factory_project / ".factory" / "archive" / "experiments"
        content = (archive_dir / "my-project-001.md").read_text()
        assert "Partial experiment" in content
        assert "N/A" in content


class TestGenerateNote:
    def test_basic_note(self, tmp_path: Path) -> None:
        exp_dir = tmp_path / "001"
        exp_dir.mkdir()
        (exp_dir / "hypothesis.md").write_text("Add caching layer")
        (exp_dir / "verdict.json").write_text(json.dumps({
            "verdict": "keep",
            "change_summary": "Added Redis cache",
            "timestamp": "2026-01-15T10:00:00",
            "delta": 0.15,
            "notes": "Performance improved significantly",
        }))

        note = _generate_note("test-project", 1, exp_dir, None)

        assert "# Experiment 001 — test-project" in note
        assert "Add caching layer" in note
        assert "**Verdict:** keep" in note
        assert "+0.1500" in note
        assert "Added Redis cache" in note

    def test_note_without_artifacts(self, tmp_path: Path) -> None:
        exp_dir = tmp_path / "001"
        exp_dir.mkdir()

        note = _generate_note("test-project", 1, exp_dir, None)

        assert "# Experiment 001 — test-project" in note
        assert "N/A" in note


class TestCLIIntegration:
    def test_parser_accepts_backfill_archive(self) -> None:
        from factory.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["backfill-archive", "/some/path"])
        assert args.command == "backfill-archive"
        assert args.path == "/some/path"

    def test_cli_backfill_archive(self, factory_project: Path, capsys: pytest.CaptureFixture) -> None:
        _add_experiment(factory_project, 1)
        _add_experiment(factory_project, 2)

        exit_code = main(["backfill-archive", str(factory_project)])

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "0 existed" in captured.out
        assert "2 created" in captured.out
        assert "2 total" in captured.out
