"""Tests for universal growth eval dimensions (factory/eval/growth.py)
and their injection by the eval runner."""

import csv
from pathlib import Path
from unittest.mock import patch

from factory.eval.growth import (
    GROWTH_WEIGHTS,
    compute_growth_results,
    eval_capability_surface,
    eval_experiment_diversity,
    eval_factory_effectiveness,
    eval_observability,
    eval_research_grounding,
)
from factory.eval.runner import _merge_all
from factory.models import EvalResult


# ── Shared helpers ───────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent


def _valid_result(result: dict) -> None:
    """Assert that a result dict has the required keys and types."""
    assert "name" in result
    assert "score" in result
    assert "weight" in result
    assert "passed" in result
    assert "details" in result
    assert isinstance(result["score"], float)
    assert 0.0 <= result["score"] <= 1.0
    assert isinstance(result["weight"], float)
    assert isinstance(result["passed"], bool)
    assert isinstance(result["details"], str)


def _write_tsv(path: Path, rows: list[dict]) -> None:
    """Write a minimal results.tsv file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["id", "hypothesis", "verdict", "delta", "change_summary"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, dialect="excel-tab")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


# ── Growth weight sum ────────────────────────────────────────────


class TestGrowthWeights:
    def test_weights_sum_to_one(self):
        """Growth weights (before runner normalization) should sum to 1.0."""
        assert abs(sum(GROWTH_WEIGHTS.values()) - 1.0) < 1e-9

    def test_five_dimensions(self):
        assert len(GROWTH_WEIGHTS) == 5

    def test_compute_growth_results_returns_five(self):
        results = compute_growth_results(PROJECT_ROOT)
        assert len(results) == 5
        names = {r["name"] for r in results}
        assert names == {
            "capability_surface", "experiment_diversity", "observability",
            "research_grounding", "factory_effectiveness",
        }


# ── Runner merge ─────────────────────────────────────────────────


class TestMergeWithGrowth:
    def test_normalizes_to_50_50(self):
        """Hygiene gets 50%, growth gets 50%."""
        hygiene_results = [
            EvalResult(name="tests", score=1.0, weight=0.6, passed=True, details="ok"),
            EvalResult(name="lint", score=1.0, weight=0.4, passed=True, details="ok"),
        ]
        growth_results = [
            EvalResult(**r) for r in compute_growth_results(PROJECT_ROOT)
        ]
        merged = _merge_all(hygiene_results, [], growth_results)

        hygiene_weight = sum(r.weight for r in merged if r.name in {"tests", "lint"})
        growth_weight = sum(
            r.weight for r in merged
            if r.name not in {"tests", "lint"}
        )

        assert abs(hygiene_weight - 0.50) < 1e-9
        assert abs(growth_weight - 0.50) < 1e-9
        assert abs(hygiene_weight + growth_weight - 1.0) < 1e-9

    def test_total_dimensions(self):
        """Should be hygiene dims + 5 growth dims."""
        hygiene_results = [
            EvalResult(name="tests", score=1.0, weight=0.5, passed=True, details=""),
            EvalResult(name="lint", score=1.0, weight=0.5, passed=True, details=""),
        ]
        growth_results = [
            EvalResult(**r) for r in compute_growth_results(PROJECT_ROOT)
        ]
        merged = _merge_all(hygiene_results, [], growth_results)
        assert len(merged) == 7  # 2 hygiene + 5 growth

    def test_project_additions_merged(self):
        """Project-specific additions are merged into the hygiene half."""
        hygiene_results = [
            EvalResult(name="tests", score=1.0, weight=0.5, passed=True, details=""),
            EvalResult(name="lint", score=1.0, weight=0.5, passed=True, details=""),
        ]
        project_results = [
            EvalResult(name="ui_renders", score=0.9, weight=0.5, passed=True, details="ok"),
        ]
        growth_results = [
            EvalResult(**r) for r in compute_growth_results(PROJECT_ROOT)
        ]
        merged = _merge_all(hygiene_results, project_results, growth_results)
        assert len(merged) == 8  # 2 hygiene + 1 project + 5 growth
        names = {r.name for r in merged}
        assert "ui_renders" in names

    def test_preserves_scores(self):
        """Scores should not be altered by merging."""
        hygiene_results = [
            EvalResult(name="tests", score=0.75, weight=1.0, passed=True, details=""),
        ]
        growth_results = [
            EvalResult(**r) for r in compute_growth_results(PROJECT_ROOT)
        ]
        merged = _merge_all(hygiene_results, [], growth_results)
        test_result = next(r for r in merged if r.name == "tests")
        assert test_result.score == 0.75


# ── capability_surface ───────────────────────────────────────────


class TestCapabilitySurface:
    def test_returns_valid_result(self):
        result = eval_capability_surface(PROJECT_ROOT)
        _valid_result(result)
        assert result["name"] == "capability_surface"

    def test_counts_real_modules(self):
        result = eval_capability_surface(PROJECT_ROOT)
        assert result["score"] > 0.0
        assert "modules=" in result["details"]

    def test_counts_public_functions(self):
        result = eval_capability_surface(PROJECT_ROOT)
        assert "public_fns=" in result["details"]

    def test_error_handling(self):
        result = eval_capability_surface(Path("/nonexistent/path"))
        _valid_result(result)

    def test_scales_target_to_project_size(self):
        """Target should scale with project size, not be a fixed constant."""
        result = eval_capability_surface(PROJECT_ROOT)
        assert "target=" in result["details"]


# ── experiment_diversity ─────────────────────────────────────────


class TestExperimentDiversity:
    def test_returns_valid_result(self):
        result = eval_experiment_diversity(PROJECT_ROOT)
        _valid_result(result)
        assert result["name"] == "experiment_diversity"

    def test_no_history_returns_neutral(self, tmp_path):
        result = eval_experiment_diversity(tmp_path)
        assert result["score"] == 0.5
        assert result["passed"] is True

    def test_few_experiments_returns_neutral(self, tmp_path):
        _write_tsv(tmp_path / ".factory" / "results.tsv", [
            {"id": "1", "hypothesis": "Add logging"},
            {"id": "2", "hypothesis": "Fix crash"},
        ])
        result = eval_experiment_diversity(tmp_path)
        assert result["score"] == 0.5

    def test_diverse_experiments_score_high(self, tmp_path):
        hypotheses = [
            "Fix crash in parser",
            "Add structured logging",
            "Increase test coverage for cli",
            "Add new search feature",
            "Refactor store module",
            "Improve eval scoring accuracy",
            "Add performance benchmarks",
            "Fix type errors in models",
            "Add agent memory system",
            "Improve prompt for strategist",
        ]
        rows = [{"id": str(i), "hypothesis": h} for i, h in enumerate(hypotheses)]
        _write_tsv(tmp_path / ".factory" / "results.tsv", rows)
        result = eval_experiment_diversity(tmp_path)
        assert result["score"] >= 0.5

    def test_repeated_experiments_penalized(self, tmp_path):
        rows = [
            {"id": str(i), "hypothesis": f"Fix bug #{i} in parser"}
            for i in range(10)
        ]
        _write_tsv(tmp_path / ".factory" / "results.tsv", rows)
        result = eval_experiment_diversity(tmp_path)
        assert result["score"] < 0.1

    def test_dominance_penalized(self, tmp_path):
        rows = [
            {"id": "0", "hypothesis": "Fix crash in parser"},
            {"id": "1", "hypothesis": "Fix bug in store"},
            {"id": "2", "hypothesis": "Fix error handling"},
            {"id": "3", "hypothesis": "Fix regression in cli"},
            {"id": "4", "hypothesis": "Fix type mismatch"},
            {"id": "5", "hypothesis": "Fix validation bug"},
            {"id": "6", "hypothesis": "Fix timeout issue"},
            {"id": "7", "hypothesis": "Fix encoding problem"},
            {"id": "8", "hypothesis": "Add structured logging"},
            {"id": "9", "hypothesis": "Increase test coverage"},
        ]
        _write_tsv(tmp_path / ".factory" / "results.tsv", rows)
        result = eval_experiment_diversity(tmp_path)
        assert "dominant=" in result["details"]
        assert result["score"] < 0.6


# ── observability ────────────────────────────────────────────────


class TestObservability:
    def test_returns_valid_result(self):
        result = eval_observability(PROJECT_ROOT)
        _valid_result(result)
        assert result["name"] == "observability"

    def test_score_between_0_and_1(self):
        result = eval_observability(PROJECT_ROOT)
        assert 0.0 <= result["score"] <= 1.0

    def test_details_contain_metrics(self):
        result = eval_observability(PROJECT_ROOT)
        assert "observability_score=" in result["details"]
        assert "function_coverage=" in result["details"]

    def test_error_handling(self):
        with patch(
            "factory.study._analyze_observability",
            side_effect=RuntimeError("test"),
        ):
            result = eval_observability(PROJECT_ROOT)
            _valid_result(result)
            assert result["score"] == 0.0


# ── research_grounding ───────────────────────────────────────────


class TestResearchGrounding:
    def test_returns_valid_result(self):
        result = eval_research_grounding(PROJECT_ROOT)
        _valid_result(result)
        assert result["name"] == "research_grounding"

    def test_rewards_vault_sources(self):
        result = eval_research_grounding(PROJECT_ROOT)
        assert result["score"] > 0.0
        assert "sources=" in result["details"]

    def test_no_vault_low_score(self, tmp_path):
        with patch("pathlib.Path.home", return_value=tmp_path / "fakehome"):
            result = eval_research_grounding(tmp_path)
            _valid_result(result)
            assert result["score"] <= 0.15

    def test_with_source_notes(self, tmp_path):
        vault = tmp_path / "obsidian-vaults" / "factory"
        sources = vault / "20-Knowledge" / "Sources"
        sources.mkdir(parents=True)
        for i in range(8):
            (sources / f"source-name-{i}.md").write_text("Content")
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = eval_research_grounding(tmp_path)
            assert result["score"] >= 0.2

    def test_doc_ratio_with_experiments_subdirectory(self, tmp_path):
        """doc_ratio counts notes in Experiments/ subdirectory."""
        vault = tmp_path / "obsidian-vaults" / "factory"
        exp_dir = vault / "10-Projects" / tmp_path.name / "Experiments"
        exp_dir.mkdir(parents=True)
        for i in range(4):
            (exp_dir / f"{tmp_path.name}-{i:03d}.md").write_text("note")
        # Create 4 factory experiments so ratio = 4/4 = 1.0
        factory_exp = tmp_path / ".factory" / "experiments"
        for i in range(4):
            (factory_exp / f"{i:03d}").mkdir(parents=True)
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = eval_research_grounding(tmp_path)
            assert "doc_ratio=" in result["details"]
            assert "4/4" in result["details"]

    def test_doc_ratio_with_flat_exp_files(self, tmp_path):
        """doc_ratio falls back to flat Exp-*.md files at project level."""
        vault = tmp_path / "obsidian-vaults" / "factory"
        project_vault = vault / "10-Projects" / tmp_path.name
        project_vault.mkdir(parents=True)
        # Write flat Exp-*.md files (legacy layout)
        for i in range(6):
            (project_vault / f"Exp-{i:03d}.md").write_text("note")
        # Create 6 factory experiments
        factory_exp = tmp_path / ".factory" / "experiments"
        for i in range(6):
            (factory_exp / f"{i:03d}").mkdir(parents=True)
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = eval_research_grounding(tmp_path)
            assert "doc_ratio=" in result["details"]
            assert "6/6" in result["details"]

    def test_doc_ratio_prefers_max_of_both_layouts(self, tmp_path):
        """doc_ratio uses max(subdirectory count, flat count)."""
        vault = tmp_path / "obsidian-vaults" / "factory"
        project_vault = vault / "10-Projects" / tmp_path.name
        # Subdirectory has 2 notes
        exp_dir = project_vault / "Experiments"
        exp_dir.mkdir(parents=True)
        for i in range(2):
            (exp_dir / f"{tmp_path.name}-{i:03d}.md").write_text("note")
        # Flat files have 5 notes
        for i in range(5):
            (project_vault / f"Exp-{i:03d}.md").write_text("note")
        # 5 factory experiments
        factory_exp = tmp_path / ".factory" / "experiments"
        for i in range(5):
            (factory_exp / f"{i:03d}").mkdir(parents=True)
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = eval_research_grounding(tmp_path)
            # Should use max(2, 5) = 5
            assert "5/5" in result["details"]

    def test_no_generic_tag_matching(self, tmp_path):
        vault = tmp_path / "obsidian-vaults" / "factory"
        sources = vault / "20-Knowledge" / "Sources"
        sources.mkdir(parents=True)
        (sources / "test-source.md").write_text(
            "---\ntags:\n  - research\n  - building\n  - source\n---\nContent"
        )
        factory_dir = tmp_path / ".factory"
        _write_tsv(factory_dir / "results.tsv", [
            {"id": "1", "hypothesis": "Add research capabilities"},
            {"id": "2", "hypothesis": "Improve building process"},
            {"id": "3", "hypothesis": "Fix source loading"},
        ])
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = eval_research_grounding(tmp_path)
            assert result["score"] < 0.5


# ── factory_effectiveness ────────────────────────────────────────


class TestFactoryEffectiveness:
    def test_returns_valid_result(self):
        result = eval_factory_effectiveness(PROJECT_ROOT)
        _valid_result(result)
        assert result["name"] == "factory_effectiveness"

    def test_no_history_returns_neutral(self, tmp_path):
        result = eval_factory_effectiveness(tmp_path)
        assert result["score"] == 0.5
        assert result["passed"] is True

    def test_few_experiments_returns_neutral(self, tmp_path):
        _write_tsv(tmp_path / ".factory" / "results.tsv", [
            {"id": "1", "hypothesis": "Add logging", "verdict": "keep"},
        ])
        result = eval_factory_effectiveness(tmp_path)
        assert result["score"] == 0.5

    def test_high_keep_rate_scores_well(self, tmp_path):
        rows = [
            {"id": str(i), "hypothesis": f"Exp {i}", "verdict": "keep", "delta": "0.01"}
            for i in range(8)
        ]
        _write_tsv(tmp_path / ".factory" / "results.tsv", rows)
        result = eval_factory_effectiveness(tmp_path)
        assert result["score"] >= 0.5
        assert "keep_rate=1.00" in result["details"]

    def test_low_keep_rate_penalized(self, tmp_path):
        rows = [
            {"id": str(i), "hypothesis": f"Exp {i}", "verdict": "revert", "delta": "-0.01"}
            for i in range(8)
        ]
        _write_tsv(tmp_path / ".factory" / "results.tsv", rows)
        result = eval_factory_effectiveness(tmp_path)
        assert result["score"] < 0.3

    def test_multi_project_detection(self, tmp_path):
        projects = tmp_path / "factory-projects"
        for name in ["proj-a", "proj-b", "proj-c"]:
            (projects / name / ".factory").mkdir(parents=True)
            _write_tsv(projects / name / ".factory" / "results.tsv", [
                {"id": "1", "hypothesis": "test", "verdict": "keep"},
            ])
        main_tsv_rows = [
            {"id": str(i), "hypothesis": f"Exp {i}", "verdict": "keep", "delta": "0.01"}
            for i in range(8)
        ]
        _write_tsv(tmp_path / ".factory" / "results.tsv", main_tsv_rows)
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = eval_factory_effectiveness(tmp_path)
            assert "managed_projects=3" in result["details"]
