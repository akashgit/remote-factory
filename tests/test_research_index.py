"""Tests for factory.research_index — citation tracking for experiments."""

import csv
from io import StringIO
from pathlib import Path

from factory.cli import cmd_research
from factory.research_index import (
    build_citation_index,
    citation_coverage,
    uncited_experiments,
)


def _write_results_tsv(project_path: Path, rows: list[dict]) -> None:
    """Write a results.tsv with the full column set including research_citations."""
    factory_dir = project_path / ".factory"
    factory_dir.mkdir(parents=True, exist_ok=True)
    tsv_path = factory_dir / "results.tsv"

    fieldnames = [
        "id", "timestamp", "hypothesis", "change_summary", "issue_number",
        "pr_number", "score_before", "score_after", "delta", "verdict",
        "cost_usd", "notes", "research_citations",
    ]
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, dialect="excel-tab")
    writer.writeheader()
    for row in rows:
        full_row = {k: row.get(k, "") for k in fieldnames}
        writer.writerow(full_row)
    tsv_path.write_text(buf.getvalue())


def _make_row(
    exp_id: int,
    hypothesis: str = "Test hypothesis",
    citations: str = "",
) -> dict:
    """Create a minimal experiment row dict."""
    return {
        "id": str(exp_id),
        "timestamp": "2025-01-01T00:00:00",
        "hypothesis": hypothesis,
        "change_summary": "Changes",
        "verdict": "keep",
        "notes": "",
        "research_citations": citations,
    }


class TestBuildCitationIndex:
    def test_empty_project(self, tmp_path: Path) -> None:
        """No .factory dir returns empty index."""
        index = build_citation_index(tmp_path)
        assert index == {}

    def test_no_citations(self, tmp_path: Path) -> None:
        """Experiments without citations produce empty index."""
        _write_results_tsv(tmp_path, [
            _make_row(1),
            _make_row(2),
        ])
        index = build_citation_index(tmp_path)
        assert index == {}

    def test_with_citations(self, tmp_path: Path) -> None:
        """Experiments with citations appear in the index."""
        _write_results_tsv(tmp_path, [
            _make_row(1, citations="https://arxiv.org/abs/1234|#42"),
            _make_row(2),
            _make_row(3, citations="Ideas/Research.md"),
        ])
        index = build_citation_index(tmp_path)
        assert 1 in index
        assert index[1] == ["https://arxiv.org/abs/1234", "#42"]
        assert 2 not in index
        assert 3 in index
        assert index[3] == ["Ideas/Research.md"]

    def test_single_citation(self, tmp_path: Path) -> None:
        """Single citation (no pipe separator) works correctly."""
        _write_results_tsv(tmp_path, [
            _make_row(1, citations="https://example.com"),
        ])
        index = build_citation_index(tmp_path)
        assert index[1] == ["https://example.com"]


class TestCitationCoverage:
    def test_empty_project(self, tmp_path: Path) -> None:
        """No experiments returns 0.0 coverage."""
        coverage = citation_coverage(tmp_path)
        assert coverage == 0.0

    def test_no_citations(self, tmp_path: Path) -> None:
        """All uncited experiments return 0.0 coverage."""
        _write_results_tsv(tmp_path, [_make_row(i) for i in range(1, 6)])
        coverage = citation_coverage(tmp_path)
        assert coverage == 0.0

    def test_partial_coverage(self, tmp_path: Path) -> None:
        """Some cited experiments return correct fraction."""
        _write_results_tsv(tmp_path, [
            _make_row(1, citations="https://example.com"),
            _make_row(2),
            _make_row(3, citations="#55"),
            _make_row(4),
            _make_row(5),
        ])
        coverage = citation_coverage(tmp_path)
        assert coverage == 2 / 5

    def test_full_coverage(self, tmp_path: Path) -> None:
        """All cited experiments return 1.0 coverage."""
        _write_results_tsv(tmp_path, [
            _make_row(i, citations=f"ref-{i}") for i in range(1, 4)
        ])
        coverage = citation_coverage(tmp_path)
        assert coverage == 1.0

    def test_uses_last_10(self, tmp_path: Path) -> None:
        """Coverage only considers the last 10 experiments."""
        rows = [_make_row(i) for i in range(1, 13)]  # 12 uncited
        rows[-1]["research_citations"] = "ref-12"  # only last is cited
        _write_results_tsv(tmp_path, rows)
        coverage = citation_coverage(tmp_path)
        assert coverage == 1 / 10  # 1 cited in last 10


class TestUncitedExperiments:
    def test_empty_project(self, tmp_path: Path) -> None:
        uncited = uncited_experiments(tmp_path)
        assert uncited == []

    def test_all_cited(self, tmp_path: Path) -> None:
        _write_results_tsv(tmp_path, [
            _make_row(1, citations="ref-1"),
            _make_row(2, citations="ref-2"),
        ])
        uncited = uncited_experiments(tmp_path)
        assert uncited == []

    def test_some_uncited(self, tmp_path: Path) -> None:
        _write_results_tsv(tmp_path, [
            _make_row(1, citations="ref-1"),
            _make_row(2),
            _make_row(3, citations="ref-3"),
            _make_row(4),
        ])
        uncited = uncited_experiments(tmp_path)
        assert uncited == [2, 4]

    def test_uses_last_10(self, tmp_path: Path) -> None:
        """Only last 10 experiments are considered."""
        rows = [_make_row(i, citations=f"ref-{i}") for i in range(1, 13)]  # 12 cited
        rows[-1]["research_citations"] = ""  # last one uncited
        _write_results_tsv(tmp_path, rows)
        uncited = uncited_experiments(tmp_path)
        assert uncited == [12]


class TestCmdResearch:
    def test_no_experiments(self, tmp_path: Path, capsys) -> None:
        import argparse

        args = argparse.Namespace(path=str(tmp_path))
        ret = cmd_research(args)
        assert ret == 0
        captured = capsys.readouterr()
        assert "No experiments recorded" in captured.out

    def test_output_format(self, tmp_path: Path, capsys) -> None:
        import argparse

        _write_results_tsv(tmp_path, [
            _make_row(1, hypothesis="Add structured logging", citations="https://example.com|#42"),
            _make_row(2, hypothesis="Fix crash in parser"),
        ])
        args = argparse.Namespace(path=str(tmp_path))
        ret = cmd_research(args)
        assert ret == 0
        captured = capsys.readouterr()
        output = captured.out

        # Check header
        assert "ID" in output
        assert "Hypothesis" in output
        assert "Citations" in output

        # Check rows
        assert "Add structured logging" in output
        assert "https://example.com" in output
        assert "#42" in output
        assert "Fix crash in parser" in output

        # Check summary
        assert "2 experiments" in output
        assert "1 cited" in output
        assert "50%" in output
