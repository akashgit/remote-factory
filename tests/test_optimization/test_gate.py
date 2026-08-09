"""Tests for factory.optimization.gate — accept/reject gate."""

from __future__ import annotations

from factory.optimization.gate import evaluate_gate


class TestEvaluateGate:
    def test_accept_when_better(self) -> None:
        result = evaluate_gate(
            candidate_score=0.8,
            current_score=0.7,
            best_score=0.7,
            best_step=1,
            global_step=2,
        )
        assert result.accepted is True
        assert result.candidate_score == 0.8
        assert result.best_score == 0.8

    def test_reject_when_worse(self) -> None:
        result = evaluate_gate(
            candidate_score=0.5,
            current_score=0.7,
            best_score=0.7,
            best_step=1,
            global_step=2,
        )
        assert result.accepted is False
        assert result.best_score == 0.7

    def test_reject_when_equal(self) -> None:
        result = evaluate_gate(
            candidate_score=0.7,
            current_score=0.7,
            best_score=0.7,
            best_step=1,
            global_step=2,
        )
        assert result.accepted is False

    def test_zero_scores(self) -> None:
        result = evaluate_gate(
            candidate_score=0.0,
            current_score=0.0,
            best_score=0.0,
            best_step=0,
            global_step=1,
        )
        assert result.accepted is False

    def test_best_score_preserved_on_reject(self) -> None:
        result = evaluate_gate(
            candidate_score=0.3,
            current_score=0.5,
            best_score=0.9,
            best_step=5,
            global_step=10,
        )
        assert result.accepted is False
        assert result.best_score == 0.9

    def test_reason_contains_scores(self) -> None:
        result = evaluate_gate(
            candidate_score=0.85,
            current_score=0.80,
            best_score=0.80,
            best_step=1,
            global_step=2,
        )
        assert "0.85" in result.reason
        assert "0.80" in result.reason
