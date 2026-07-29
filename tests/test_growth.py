"""Tests for factory.eval.growth — growth eval dimensions."""

import csv
import io
from pathlib import Path
from unittest.mock import patch

from factory.eval.growth import (
    _discover_managed_projects,
    eval_experiment_diversity,
    eval_factory_effectiveness,
    eval_research_grounding,
)
from factory.store import TSV_COLUMNS


def _make_managed_project(path: Path) -> None:
    """Create a minimal .factory/results.tsv in a directory."""
    factory_dir = path / ".factory"
    factory_dir.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    writer = csv.writer(buf, dialect="excel-tab")
    writer.writerow([
        "id", "timestamp", "hypothesis", "change_summary", "issue_number",
        "pr_number", "score_before", "score_after", "delta", "verdict",
        "cost_usd", "notes", "research_citations",
    ])
    for i in range(1, 5):
        writer.writerow([
            i, "2025-01-01T00:00:00", f"H{i}", f"change {i}", "", "",
            "0.7", "0.8", "0.1", "keep", "", "", "",
        ])
    (factory_dir / "results.tsv").write_text(buf.getvalue())


class TestDiscoverManagedProjects:
    def test_sibling_discovery(self, tmp_path):
        """Projects in the same parent dir are discovered as siblings."""
        project_a = tmp_path / "project-a"
        project_b = tmp_path / "project-b"
        project_c = tmp_path / "project-c"
        project_a.mkdir()
        project_b.mkdir()
        project_c.mkdir()

        _make_managed_project(project_b)
        _make_managed_project(project_c)

        count = _discover_managed_projects(project_a)
        assert count == 2

    def test_does_not_count_self(self, tmp_path):
        """The current project should not count itself."""
        project = tmp_path / "my-project"
        project.mkdir()
        _make_managed_project(project)

        count = _discover_managed_projects(project)
        assert count == 0

    def test_env_var_factory_managed_dirs(self, tmp_path, monkeypatch):
        """FACTORY_MANAGED_DIRS env var adds extra directories to scan."""
        project = tmp_path / "workspace" / "my-project"
        project.mkdir(parents=True)

        extra_dir = tmp_path / "extra-projects"
        extra_dir.mkdir()
        _make_managed_project(extra_dir / "proj-x")
        _make_managed_project(extra_dir / "proj-y")

        monkeypatch.setenv("FACTORY_MANAGED_DIRS", str(extra_dir))
        monkeypatch.delenv("FACTORY_PROJECTS_DIR", raising=False)

        count = _discover_managed_projects(project)
        assert count == 2

    def test_env_var_colon_separated(self, tmp_path, monkeypatch):
        """FACTORY_MANAGED_DIRS supports colon-separated paths."""
        project = tmp_path / "workspace" / "my-project"
        project.mkdir(parents=True)

        dir_a = tmp_path / "dir-a"
        dir_b = tmp_path / "dir-b"
        dir_a.mkdir()
        dir_b.mkdir()
        _make_managed_project(dir_a / "p1")
        _make_managed_project(dir_b / "p2")

        monkeypatch.setenv("FACTORY_MANAGED_DIRS", f"{dir_a}:{dir_b}")
        monkeypatch.delenv("FACTORY_PROJECTS_DIR", raising=False)

        count = _discover_managed_projects(project)
        assert count == 2

    def test_deduplication(self, tmp_path, monkeypatch):
        """Same project found via multiple sources counts only once."""
        parent = tmp_path / "workspace"
        parent.mkdir()
        project = parent / "my-project"
        project.mkdir()
        sibling = parent / "sibling"
        sibling.mkdir()
        _make_managed_project(sibling)

        monkeypatch.setenv("FACTORY_MANAGED_DIRS", str(parent))
        monkeypatch.delenv("FACTORY_PROJECTS_DIR", raising=False)

        count = _discover_managed_projects(project)
        assert count == 1

    def test_legacy_factory_projects_dir(self, tmp_path, monkeypatch):
        """FACTORY_PROJECTS_DIR (legacy) is still supported."""
        project = tmp_path / "workspace" / "my-project"
        project.mkdir(parents=True)

        legacy_dir = tmp_path / "factory-projects"
        legacy_dir.mkdir()
        _make_managed_project(legacy_dir / "old-proj")

        monkeypatch.delenv("FACTORY_MANAGED_DIRS", raising=False)
        monkeypatch.setenv("FACTORY_PROJECTS_DIR", str(legacy_dir))

        count = _discover_managed_projects(project)
        assert count == 1

    def test_nonexistent_dirs_ignored(self, tmp_path, monkeypatch):
        """Non-existent paths in FACTORY_MANAGED_DIRS are silently ignored."""
        project = tmp_path / "my-project"
        project.mkdir()

        monkeypatch.setenv("FACTORY_MANAGED_DIRS", "/nonexistent/path:/also/missing")
        monkeypatch.delenv("FACTORY_PROJECTS_DIR", raising=False)

        count = _discover_managed_projects(project)
        assert count == 0


class TestEvalFactoryEffectiveness:
    def test_sibling_projects_improve_score(self, tmp_path, monkeypatch):
        """factory_effectiveness score increases when sibling managed projects exist."""
        monkeypatch.delenv("FACTORY_MANAGED_DIRS", raising=False)
        monkeypatch.delenv("FACTORY_PROJECTS_DIR", raising=False)

        parent = tmp_path / "projects"
        parent.mkdir()
        project = parent / "main-project"
        project.mkdir()
        _make_managed_project(project)

        result_alone = eval_factory_effectiveness(project)

        _make_managed_project(parent / "sibling-1")
        _make_managed_project(parent / "sibling-2")
        _make_managed_project(parent / "sibling-3")

        result_with_siblings = eval_factory_effectiveness(project)

        assert result_with_siblings["score"] > result_alone["score"]
        assert "managed_projects=3" in result_with_siblings["details"]

    def test_no_results_tsv(self, tmp_path, monkeypatch):
        """Returns neutral score when no results.tsv exists."""
        monkeypatch.delenv("FACTORY_MANAGED_DIRS", raising=False)
        monkeypatch.delenv("FACTORY_PROJECTS_DIR", raising=False)

        project = tmp_path / "empty-project"
        project.mkdir()
        result = eval_factory_effectiveness(project)
        assert result["score"] == 0.5
        assert result["passed"] is True

    def test_env_var_increases_managed_count(self, tmp_path, monkeypatch):
        """FACTORY_MANAGED_DIRS env var contributes to managed project count."""
        parent = tmp_path / "workspace"
        parent.mkdir()
        project = parent / "my-proj"
        project.mkdir()
        _make_managed_project(project)

        extra = tmp_path / "extra"
        extra.mkdir()
        _make_managed_project(extra / "e1")
        _make_managed_project(extra / "e2")

        monkeypatch.setenv("FACTORY_MANAGED_DIRS", str(extra))
        monkeypatch.delenv("FACTORY_PROJECTS_DIR", raising=False)

        result = eval_factory_effectiveness(project)
        assert "managed_projects=2" in result["details"]


def _write_tsv_no_header(project_path: Path, data_rows: list[list[str]]) -> None:
    """Write results.tsv without a header row (legacy format)."""
    factory_dir = project_path / ".factory"
    factory_dir.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    writer = csv.writer(buf, dialect="excel-tab")
    for row in data_rows:
        writer.writerow(row)
    (factory_dir / "results.tsv").write_text(buf.getvalue())


def _write_tsv_with_header(project_path: Path, data_rows: list[list[str]]) -> None:
    """Write results.tsv with a header row."""
    factory_dir = project_path / ".factory"
    factory_dir.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    writer = csv.writer(buf, dialect="excel-tab")
    writer.writerow(TSV_COLUMNS)
    for row in data_rows:
        writer.writerow(row)
    (factory_dir / "results.tsv").write_text(buf.getvalue())


def _sample_row(
    exp_id: int, hypothesis: str, verdict: str = "keep",
) -> list[str]:
    return [
        str(exp_id), "2025-01-01T00:00:00", hypothesis, "changes",
        "", "", "0.7", "0.8", "0.1", verdict, "", "", "",
    ]


class TestEvalExperimentDiversity:
    def test_diverse_hypotheses_score_high(self, tmp_path):
        _write_tsv_with_header(tmp_path, [
            _sample_row(1, "Fix crash in parser"),
            _sample_row(2, "Add structured logging"),
            _sample_row(3, "Refactor auth module"),
            _sample_row(4, "Add new endpoint for users"),
            _sample_row(5, "Improve test coverage"),
            _sample_row(6, "Fix mypy type errors"),
            _sample_row(7, "Add CI pipeline"),
            _sample_row(8, "Optimize query performance"),
            _sample_row(9, "Update eval scoring"),
            _sample_row(10, "Fix agent timeout issue"),
        ])
        result = eval_experiment_diversity(tmp_path)
        assert result["score"] > 0.4
        assert "distinct categories" in result["details"]

    def test_all_same_category_scores_low(self, tmp_path):
        _write_tsv_with_header(tmp_path, [
            _sample_row(i, f"Add feature {i}") for i in range(1, 11)
        ])
        result = eval_experiment_diversity(tmp_path)
        assert result["score"] < 0.3

    def test_headerless_tsv_reads_correctly(self, tmp_path):
        """TSV without header row should still classify hypotheses correctly."""
        _write_tsv_no_header(tmp_path, [
            _sample_row(1, "Fix crash in parser"),
            _sample_row(2, "Add structured logging"),
            _sample_row(3, "Refactor module"),
            _sample_row(4, "Add new endpoint"),
        ])
        result = eval_experiment_diversity(tmp_path)
        assert result["score"] > 0.4
        assert "distinct categories" in result["details"]

    def test_headerless_tsv_with_enough_rows(self, tmp_path):
        """Headerless TSV with 10+ rows correctly classifies hypotheses."""
        _write_tsv_no_header(tmp_path, [
            _sample_row(1, "Fix crash in parser"),
            _sample_row(2, "Add structured logging to core"),
            _sample_row(3, "Refactor auth module"),
            _sample_row(4, "Add new endpoint for users"),
            _sample_row(5, "Improve test coverage"),
            _sample_row(6, "Fix mypy type errors"),
            _sample_row(7, "Add CI pipeline"),
            _sample_row(8, "Optimize query performance"),
            _sample_row(9, "Update eval scoring"),
            _sample_row(10, "Fix agent timeout issue"),
        ])
        result = eval_experiment_diversity(tmp_path)
        assert result["score"] > 0.4
        assert "Error" not in result["details"]


class TestEvalResearchGrounding:
    def test_no_error_with_headerless_tsv(self, tmp_path):
        """research_grounding should not KeyError on headerless results.tsv."""
        _write_tsv_no_header(tmp_path, [
            _sample_row(i, f"Hypothesis {i}") for i in range(1, 6)
        ])
        (tmp_path / ".factory" / "archive").mkdir(parents=True, exist_ok=True)
        with patch("factory.obsidian.notes.vault_path", return_value=None):
            result = eval_research_grounding(tmp_path)
        assert "Error" not in result["details"]
        assert result["score"] >= 0.0

    def test_no_error_with_header_tsv(self, tmp_path):
        """research_grounding works normally with headered results.tsv."""
        _write_tsv_with_header(tmp_path, [
            _sample_row(i, f"Hypothesis {i}") for i in range(1, 6)
        ])
        (tmp_path / ".factory" / "archive").mkdir(parents=True, exist_ok=True)
        with patch("factory.obsidian.notes.vault_path", return_value=None):
            result = eval_research_grounding(tmp_path)
        assert "Error" not in result["details"]
