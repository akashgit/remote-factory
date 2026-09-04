"""Tests for the VerifyResult → EvalResult adapter."""

from __future__ import annotations

from factory.outer_loop.verify_adapter import eval_result_from_verify_results
from factory.task import VerifyResult


class TestEvalResultFromVerifyResults:
    def test_empty_list(self) -> None:
        result = eval_result_from_verify_results([])
        assert result.score == 0.0
        assert result.benchmark_score == 0.0
        assert result.details["verify_count"] == 0
        assert result.details["passed_count"] == 0
        assert result.details["failed_count"] == 0

    def test_single_pass(self) -> None:
        vr = VerifyResult(passed=True, score=1.0, details={"method": "exit_code"})
        result = eval_result_from_verify_results([vr])
        assert result.score == 1.0
        assert result.benchmark_score == 1.0
        assert result.details["verify_count"] == 1
        assert result.details["passed_count"] == 1
        assert result.details["failed_count"] == 0
        instances = result.details["instance_results"]
        assert isinstance(instances, list)
        assert len(instances) == 1
        assert instances[0]["passed"] is True
        assert instances[0]["score"] == 1.0
        assert instances[0]["details"] == {"method": "exit_code"}

    def test_single_fail(self) -> None:
        vr = VerifyResult(passed=False, score=0.0)
        result = eval_result_from_verify_results([vr])
        assert result.score == 0.0
        assert result.details["passed_count"] == 0
        assert result.details["failed_count"] == 1

    def test_mixed_results_mean_score(self) -> None:
        results = [
            VerifyResult(passed=True, score=1.0),
            VerifyResult(passed=False, score=0.0),
            VerifyResult(passed=True, score=0.5),
        ]
        result = eval_result_from_verify_results(results)
        assert result.score == 0.5
        assert result.details["verify_count"] == 3
        assert result.details["passed_count"] == 2
        assert result.details["failed_count"] == 1

    def test_partial_scores(self) -> None:
        results = [
            VerifyResult(passed=True, score=0.8),
            VerifyResult(passed=True, score=0.6),
        ]
        result = eval_result_from_verify_results(results)
        assert result.score == 0.7
        assert result.details["passed_count"] == 2

    def test_details_preserved(self) -> None:
        vr = VerifyResult(
            passed=True,
            score=0.9,
            details={"returncode": 0, "scoring_contract": "json"},
        )
        result = eval_result_from_verify_results([vr])
        instances = result.details["instance_results"]
        assert instances[0]["details"]["returncode"] == 0
        assert instances[0]["details"]["scoring_contract"] == "json"

    def test_empty_details_omitted(self) -> None:
        vr = VerifyResult(passed=True, score=1.0)
        result = eval_result_from_verify_results([vr])
        instances = result.details["instance_results"]
        assert "details" not in instances[0]

    def test_result_is_valid_eval_result(self) -> None:
        from factory.outer_loop.models import EvalResult

        vr = VerifyResult(passed=True, score=0.75)
        result = eval_result_from_verify_results([vr])
        assert isinstance(result, EvalResult)
        assert result.cost_usd == 0.0
        assert result.complexity == 0.0
        assert result.hygiene_score == 0.0


class TestVerifyDataReachesEvalResult:
    """Integration-level: verify data from instance_results flows through
    the adapter and lands in EvalResult.details when called via the
    evaluator path."""

    def test_verify_data_in_details(self) -> None:
        """Simulate the evaluator's reconstruction path:
        dict → VerifyResult → adapter → EvalResult.details["verify"]."""
        instance_results = [
            {"passed": True, "score": 1.0, "details": {"method": "exit_code"}},
            {"passed": False, "score": 0.0, "details": {"error": "timeout"}},
        ]

        verify_results = []
        for ir in instance_results:
            verify_results.append(VerifyResult(
                passed=bool(ir.get("passed", False)),
                score=float(ir.get("score", 0.0)),
                details=ir.get("details", {}),
            ))

        adapted = eval_result_from_verify_results(verify_results)

        assert adapted.details["verify_count"] == 2
        assert adapted.details["passed_count"] == 1
        assert adapted.details["failed_count"] == 1
        assert adapted.score == 0.5

        instances = adapted.details["instance_results"]
        assert instances[0]["passed"] is True
        assert instances[1]["details"]["error"] == "timeout"
