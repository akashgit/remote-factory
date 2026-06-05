"""Tests for within-tier weight override normalization."""

from factory.eval.runner import _normalize_tier
from factory.models import EvalResult


class TestNormalizeTierOverrides:
    def test_no_overrides_preserves_behavior(self):
        results = [
            EvalResult(name="a", score=1.0, weight=0.6, passed=True, details=""),
            EvalResult(name="b", score=0.5, weight=0.4, passed=True, details=""),
        ]
        normalized = _normalize_tier(results, 0.5)
        assert len(normalized) == 2
        assert abs(sum(r.weight for r in normalized) - 0.5) < 1e-9

    def test_sparse_override_changes_relative_weights(self):
        results = [
            EvalResult(name="tests", score=1.0, weight=0.30, passed=True, details=""),
            EvalResult(name="lint", score=0.8, weight=0.15, passed=True, details=""),
            EvalResult(name="coverage", score=0.6, weight=0.25, passed=True, details=""),
        ]
        overrides = {"tests": 0.50}
        normalized = _normalize_tier(results, 1.0, weight_overrides=overrides)
        assert len(normalized) == 3
        assert abs(sum(r.weight for r in normalized) - 1.0) < 1e-9
        tests_r = next(r for r in normalized if r.name == "tests")
        lint_r = next(r for r in normalized if r.name == "lint")
        assert tests_r.weight > lint_r.weight

    def test_override_all_dimensions(self):
        results = [
            EvalResult(name="a", score=1.0, weight=0.5, passed=True, details=""),
            EvalResult(name="b", score=0.5, weight=0.5, passed=True, details=""),
        ]
        overrides = {"a": 3.0, "b": 1.0}
        normalized = _normalize_tier(results, 1.0, weight_overrides=overrides)
        a_r = next(r for r in normalized if r.name == "a")
        b_r = next(r for r in normalized if r.name == "b")
        assert abs(a_r.weight - 0.75) < 1e-9
        assert abs(b_r.weight - 0.25) < 1e-9

    def test_override_nonexistent_dim_ignored(self):
        results = [
            EvalResult(name="a", score=1.0, weight=0.5, passed=True, details=""),
        ]
        overrides = {"nonexistent": 99.0}
        normalized = _normalize_tier(results, 1.0, weight_overrides=overrides)
        assert len(normalized) == 1
        assert abs(normalized[0].weight - 1.0) < 1e-9

    def test_empty_results_returns_empty(self):
        assert _normalize_tier([], 0.5, weight_overrides={"a": 1.0}) == []

    def test_zero_target_returns_empty(self):
        results = [
            EvalResult(name="a", score=1.0, weight=1.0, passed=True, details=""),
        ]
        assert _normalize_tier(results, 0.0, weight_overrides={"a": 2.0}) == []

    def test_scores_preserved_through_override(self):
        results = [
            EvalResult(name="a", score=0.9, weight=0.5, passed=True, details="detail_a"),
            EvalResult(name="b", score=0.3, weight=0.5, passed=False, details="detail_b"),
        ]
        overrides = {"a": 2.0}
        normalized = _normalize_tier(results, 1.0, weight_overrides=overrides)
        a_r = next(r for r in normalized if r.name == "a")
        b_r = next(r for r in normalized if r.name == "b")
        assert a_r.score == 0.9
        assert b_r.score == 0.3
        assert a_r.details == "detail_a"
        assert a_r.passed is True
        assert b_r.passed is False
