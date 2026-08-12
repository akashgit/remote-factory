"""Tests for factory.research.store — research directory management."""

from pathlib import Path

from factory.research.runner import (
    create_run_dir,
    ensure_research_dir,
    save_run_summary,
)


class TestEnsureResearchDir:
    def test_creates_directory(self, tmp_path: Path) -> None:
        result = ensure_research_dir(tmp_path)
        assert result == tmp_path / ".factory" / "research"
        assert (tmp_path / ".factory" / "research" / "runs").is_dir()

    def test_idempotent(self, tmp_path: Path) -> None:
        ensure_research_dir(tmp_path)
        ensure_research_dir(tmp_path)
        assert (tmp_path / ".factory" / "research" / "runs").is_dir()


class TestCreateRunDir:
    def test_creates_cycle_dir(self, tmp_path: Path) -> None:
        run_dir = create_run_dir(tmp_path, "cycle-001")
        assert run_dir.is_dir()
        assert run_dir == tmp_path / ".factory" / "research" / "runs" / "cycle-001"

    def test_idempotent(self, tmp_path: Path) -> None:
        d1 = create_run_dir(tmp_path, "cycle-001")
        d2 = create_run_dir(tmp_path, "cycle-001")
        assert d1 == d2


class TestSaveRunSummary:
    def test_writes_summary(self, tmp_path: Path) -> None:
        run_dir = create_run_dir(tmp_path, "cycle-001")
        summary = {"status": "PASS", "metric_value": 0.95, "duration_seconds": 12.3}
        save_run_summary(run_dir, summary)
        assert (run_dir / "summary.json").exists()
