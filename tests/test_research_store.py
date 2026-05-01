"""Tests for factory.research.store — research directory management."""

from pathlib import Path

from factory.research.store import (
    create_run_dir,
    ensure_research_dir,
    list_runs,
    load_run_summary,
    save_run_summary,
    write_comparison,
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


class TestSaveLoadRunSummary:
    def test_round_trip(self, tmp_path: Path) -> None:
        run_dir = create_run_dir(tmp_path, "cycle-001")
        summary = {"status": "PASS", "metric_value": 0.95, "duration_seconds": 12.3}
        save_run_summary(run_dir, summary)
        loaded = load_run_summary(run_dir)
        assert loaded == summary

    def test_load_missing(self, tmp_path: Path) -> None:
        assert load_run_summary(tmp_path) is None


class TestListRuns:
    def test_empty(self, tmp_path: Path) -> None:
        assert list_runs(tmp_path) == []

    def test_no_research_dir(self, tmp_path: Path) -> None:
        assert list_runs(tmp_path) == []

    def test_sorted_order(self, tmp_path: Path) -> None:
        create_run_dir(tmp_path, "cycle-003")
        create_run_dir(tmp_path, "cycle-001")
        create_run_dir(tmp_path, "cycle-002")
        runs = list_runs(tmp_path)
        names = [r.name for r in runs]
        assert names == ["cycle-001", "cycle-002", "cycle-003"]

    def test_ignores_files(self, tmp_path: Path) -> None:
        ensure_research_dir(tmp_path)
        (tmp_path / ".factory" / "research" / "runs" / "not_a_dir.txt").write_text("")
        create_run_dir(tmp_path, "cycle-001")
        runs = list_runs(tmp_path)
        assert len(runs) == 1
        assert runs[0].name == "cycle-001"


class TestWriteComparison:
    def test_creates_comparison_file(self, tmp_path: Path) -> None:
        write_comparison(tmp_path, "cycle-002", "cycle-001", "# Comparison\nBetter.")
        path = tmp_path / ".factory" / "research" / "comparison_cycle-001_vs_cycle-002.md"
        assert path.exists()
        assert "Better." in path.read_text()
