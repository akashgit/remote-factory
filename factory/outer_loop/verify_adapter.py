"""Bridge VerifyResult (from Task.verify()) into outer loop EvalResult.

Thin mapping layer: aggregates a list of per-instance VerifyResults into
a single EvalResult with verify data packed into details.
"""

from __future__ import annotations

import statistics

from factory.outer_loop.models import EvalResult
from factory.task import VerifyResult


def eval_result_from_verify_results(results: list[VerifyResult]) -> EvalResult:
    """Aggregate VerifyResults into an EvalResult.

    score = mean of individual scores (0.0 if empty)
    benchmark_score = same as score
    details includes per-instance results, pass/fail counts, etc.
    """
    if not results:
        return EvalResult(
            score=0.0,
            benchmark_score=0.0,
            details={"verify_count": 0, "passed_count": 0, "failed_count": 0},
        )

    scores = [vr.score for vr in results]
    aggregate_score = statistics.mean(scores)

    passed_count = sum(1 for vr in results if vr.passed)
    failed_count = len(results) - passed_count

    instance_details: list[dict[str, object]] = []
    for i, vr in enumerate(results):
        entry: dict[str, object] = {
            "index": i,
            "passed": vr.passed,
            "score": vr.score,
        }
        if vr.details:
            entry["details"] = vr.details
        instance_details.append(entry)

    return EvalResult(
        score=aggregate_score,
        benchmark_score=aggregate_score,
        details={
            "verify_count": len(results),
            "passed_count": passed_count,
            "failed_count": failed_count,
            "instance_results": instance_details,
        },
    )
