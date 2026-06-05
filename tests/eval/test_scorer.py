"""Tests for factory.eval.scorer — composite score computation."""

import pytest

from factory.eval.scorer import compute_composite
from factory.models import EvalResult


class TestComputeComposite:
    def test_empty_results_no_violations(self):
        score = compute_composite([], [], threshold=0.0)
        assert score.total == 0.0
        assert score.passed is True

    def test_empty_results_with_threshold(self):
        score = compute_composite([], [], threshold=0.5)
        assert score.passed is False

    def test_single_result(self):
        results = [EvalResult(name="tests", score=0.9, weight=1.0, passed=True, details="ok")]
        score = compute_composite(results, [], threshold=0.8)
        assert score.total == pytest.approx(0.9)
        assert score.passed is True

    def test_weighted_average(self):
        results = [
            EvalResult(name="tests", score=1.0, weight=0.5, passed=True, details=""),
            EvalResult(name="lint", score=0.5, weight=0.5, passed=True, details=""),
        ]
        score = compute_composite(results, [], threshold=0.0)
        assert score.total == pytest.approx(0.75)

    def test_normalizes_weights(self):
        results = [
            EvalResult(name="a", score=1.0, weight=2.0, passed=True, details=""),
            EvalResult(name="b", score=0.0, weight=2.0, passed=False, details=""),
        ]
        score = compute_composite(results, [], threshold=0.0)
        assert score.total == pytest.approx(0.5)

    def test_fails_with_guard_violations(self):
        results = [EvalResult(name="tests", score=1.0, weight=1.0, passed=True, details="")]
        score = compute_composite(results, ["eval/ modified"], threshold=0.0)
        assert score.passed is False
        assert score.total == pytest.approx(1.0)

    def test_fails_below_threshold(self):
        results = [EvalResult(name="tests", score=0.5, weight=1.0, passed=True, details="")]
        score = compute_composite(results, [], threshold=0.8)
        assert score.passed is False
