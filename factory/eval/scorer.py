"""Composite score computation from individual eval results."""

from factory.models import CompositeScore, EvalResult


def compute_composite(
    results: list[EvalResult],
    guard_violations: list[str],
    threshold: float,
) -> CompositeScore:
    """Compute weighted composite score from individual eval results.

    - Normalizes weights if they don't sum to 1.0
    - Fails if any guard violation exists
    - Fails if total < threshold
    """
    if not results:
        return CompositeScore(
            total=0.0,
            results=results,
            guard_violations=guard_violations,
            passed=len(guard_violations) == 0 and 0.0 >= threshold,
        )

    weight_sum = sum(r.weight for r in results)
    if weight_sum > 0 and abs(weight_sum - 1.0) > 1e-9:
        results = [
            EvalResult(
                name=r.name,
                score=r.score,
                weight=r.weight / weight_sum,
                passed=r.passed,
                details=r.details,
            )
            for r in results
        ]

    total = sum(r.score * r.weight for r in results)
    passed = len(guard_violations) == 0 and total >= threshold

    return CompositeScore(
        total=total,
        results=results,
        guard_violations=guard_violations,
        passed=passed,
    )
