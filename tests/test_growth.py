"""Tests for factory.eval.growth — growth eval dimensions."""

import csv
import io
from pathlib import Path


from factory.eval.growth import (
    _discover_managed_projects,
    eval_factory_effectiveness,
)


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
