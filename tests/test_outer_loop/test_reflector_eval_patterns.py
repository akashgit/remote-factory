"""Tests for OuterLoopReflector eval-details-based pattern extraction."""

from __future__ import annotations

from factory.cycle_analyzer import AgentStep, CycleRecord
from factory.outer_loop.reflector import OuterLoopReflector


def _make_step(role: str = "builder", succeeded: bool = True) -> AgentStep:
    return AgentStep(
        order=0,
        role=role,
        started_at="2024-01-01T00:00:00",
        duration_s=10.0,
        cost_usd=0.1,
        output_tokens=100,
        succeeded=succeeded,
    )


def _make_record(
    score: float,
    eval_details: dict[str, object] | None = None,
    kept: int = 0,
    reverted: int = 0,
) -> CycleRecord:
    return CycleRecord(
        cycle_number=1,
        mode="test",
        started_at=None,
        ended_at=None,
        duration_s=10.0,
        score_start=0.0,
        score_end=score,
        score_delta=score,
        steps=[_make_step()],
        kept=kept,
        reverted=reverted,
        eval_details=eval_details,
    )


class TestEvalPatterns:
    def test_verify_failure_patterns(self) -> None:
        reflector = OuterLoopReflector(k=1)
        records = [
            ("winner", 0.9, _make_record(0.9, eval_details={
                "verify": {
                    "verify_count": 5,
                    "passed_count": 5,
                    "failed_count": 0,
                    "instance_results": [
                        {"index": i, "passed": True, "score": 1.0} for i in range(5)
                    ],
                },
            }, kept=2)),
            ("loser", 0.1, _make_record(0.1, eval_details={
                "verify": {
                    "verify_count": 5,
                    "passed_count": 2,
                    "failed_count": 3,
                    "instance_results": [
                        {"index": 0, "passed": True, "score": 1.0},
                        {"index": 1, "passed": True, "score": 1.0},
                        {"index": 2, "passed": False, "score": 0.0},
                        {"index": 3, "passed": False, "score": 0.0},
                        {"index": 4, "passed": False, "score": 0.0},
                    ],
                },
            }, reverted=1)),
        ]

        report = reflector.reflect(records, generation=0)

        verify_failures = [p for p in report.failure_patterns if "verify" in p.lower()]
        assert len(verify_failures) > 0
        assert any("3/5" in p for p in verify_failures)

    def test_verify_success_patterns(self) -> None:
        reflector = OuterLoopReflector(k=1)
        records = [
            ("winner", 0.9, _make_record(0.9, eval_details={
                "verify": {
                    "verify_count": 5,
                    "passed_count": 5,
                    "failed_count": 0,
                    "instance_results": [
                        {"index": i, "passed": True, "score": 1.0} for i in range(5)
                    ],
                },
            }, kept=2)),
            ("loser", 0.1, _make_record(0.1, reverted=1)),
        ]

        report = reflector.reflect(records, generation=0)

        verify_successes = [p for p in report.success_patterns if "verify" in p.lower()]
        assert len(verify_successes) > 0
        assert any("5/5" in p for p in verify_successes)

    def test_verify_score_comparison_suggestion(self) -> None:
        reflector = OuterLoopReflector(k=1)
        records = [
            ("winner", 0.9, _make_record(0.9, eval_details={
                "verify": {
                    "verify_count": 3,
                    "passed_count": 3,
                    "failed_count": 0,
                    "instance_results": [
                        {"index": i, "passed": True, "score": 0.9} for i in range(3)
                    ],
                },
            }, kept=2)),
            ("loser", 0.1, _make_record(0.1, eval_details={
                "verify": {
                    "verify_count": 3,
                    "passed_count": 1,
                    "failed_count": 2,
                    "instance_results": [
                        {"index": 0, "passed": True, "score": 0.5},
                        {"index": 1, "passed": False, "score": 0.1},
                        {"index": 2, "passed": False, "score": 0.1},
                    ],
                },
            }, reverted=1)),
        ]

        report = reflector.reflect(records, generation=0)

        verify_suggestions = [
            s for s in report.mutation_suggestions
            if "verify" in s.lower() and "score" in s.lower()
        ]
        assert len(verify_suggestions) > 0

    def test_verify_returncode_failure(self) -> None:
        reflector = OuterLoopReflector(k=1)
        records = [
            ("winner", 0.9, _make_record(0.9, kept=2)),
            ("loser", 0.1, _make_record(0.1, eval_details={
                "verify": {
                    "verify_count": 2,
                    "passed_count": 0,
                    "failed_count": 2,
                    "instance_results": [
                        {"index": 0, "passed": False, "score": 0.0,
                         "details": {"returncode": 1}},
                        {"index": 1, "passed": False, "score": 0.0,
                         "details": {"returncode": 2}},
                    ],
                },
            }, reverted=1)),
        ]

        report = reflector.reflect(records, generation=0)

        rc_failures = [p for p in report.failure_patterns if "returncode" in p]
        assert len(rc_failures) >= 1

    def test_test_details_failure(self) -> None:
        reflector = OuterLoopReflector(k=1)
        records = [
            ("winner", 0.9, _make_record(0.9, kept=2)),
            ("loser", 0.1, _make_record(0.1, eval_details={
                "test_details": {
                    "returncode": 1,
                    "failed": 4,
                    "total": 10,
                },
            }, reverted=1)),
        ]

        report = reflector.reflect(records, generation=0)

        test_failures = [p for p in report.failure_patterns if "test" in p.lower()]
        assert len(test_failures) > 0
        assert any("returncode=1" in p for p in test_failures)
        assert any("4/10" in p for p in test_failures)

    def test_rejection_pattern(self) -> None:
        reflector = OuterLoopReflector(k=1)
        records = [
            ("winner", 0.9, _make_record(0.9, kept=2)),
            ("loser", 0.0, _make_record(0.0, eval_details={
                "rejected": "mandatory_component_missing",
            })),
        ]

        report = reflector.reflect(records, generation=0)

        rejected = [p for p in report.failure_patterns if "rejected" in p.lower()]
        assert len(rejected) > 0
        assert any("mandatory_component_missing" in p for p in rejected)

    def test_error_pattern(self) -> None:
        reflector = OuterLoopReflector(k=1)
        records = [
            ("winner", 0.9, _make_record(0.9, kept=2)),
            ("loser", 0.0, _make_record(0.0, eval_details={
                "error": "inner_loop_eval_failed: TimeoutError",
            })),
        ]

        report = reflector.reflect(records, generation=0)

        errors = [p for p in report.failure_patterns if "error" in p.lower()]
        assert len(errors) > 0
        assert any("TimeoutError" in p for p in errors)

    def test_graceful_with_no_eval_details(self) -> None:
        """Reflector degrades gracefully when eval_details is absent."""
        reflector = OuterLoopReflector(k=1)
        records = [
            ("winner", 0.9, _make_record(0.9, eval_details=None, kept=2)),
            ("loser", 0.1, _make_record(0.1, eval_details=None, reverted=1)),
        ]

        report = reflector.reflect(records, generation=0)

        assert report.top_k_ids == ["winner"]
        assert report.bottom_k_ids == ["loser"]

    def test_graceful_with_empty_details(self) -> None:
        """Reflector handles empty eval_details dicts without errors."""
        reflector = OuterLoopReflector(k=1)
        records = [
            ("winner", 0.9, _make_record(0.9, eval_details={}, kept=2)),
            ("loser", 0.1, _make_record(0.1, eval_details={}, reverted=1)),
        ]

        report = reflector.reflect(records, generation=0)
        assert report.top_k_ids == ["winner"]

    def test_mixed_details_and_no_details(self) -> None:
        """Some individuals have eval_details, some don't."""
        reflector = OuterLoopReflector(k=1)
        records = [
            ("winner", 0.9, _make_record(0.9, eval_details={
                "verify": {
                    "verify_count": 3,
                    "passed_count": 3,
                    "failed_count": 0,
                    "instance_results": [
                        {"index": i, "passed": True, "score": 1.0} for i in range(3)
                    ],
                },
            }, kept=2)),
            ("mid", 0.5, _make_record(0.5, eval_details=None)),
            ("loser", 0.1, _make_record(0.1, eval_details=None, reverted=1)),
        ]

        report = reflector.reflect(records, generation=0)

        verify_successes = [p for p in report.success_patterns if "verify" in p.lower()]
        assert len(verify_successes) > 0

    def test_eval_patterns_additive(self) -> None:
        """Eval patterns add to existing failure/success patterns, not replace."""
        reflector = OuterLoopReflector(k=1)
        records = [
            ("winner", 0.9, _make_record(
                0.9,
                eval_details={"verify": {
                    "verify_count": 2, "passed_count": 2, "failed_count": 0,
                    "instance_results": [
                        {"index": 0, "passed": True, "score": 1.0},
                        {"index": 1, "passed": True, "score": 1.0},
                    ],
                }},
                kept=2,
            )),
            ("loser", 0.1, _make_record(
                0.1,
                eval_details={"verify": {
                    "verify_count": 2, "passed_count": 0, "failed_count": 2,
                    "instance_results": [
                        {"index": 0, "passed": False, "score": 0.0},
                        {"index": 1, "passed": False, "score": 0.0},
                    ],
                }},
                reverted=2,
            )),
        ]

        report = reflector.reflect(records, generation=0)

        non_verify_failures = [
            p for p in report.failure_patterns if "verify" not in p.lower()
        ]
        assert len(non_verify_failures) > 0, "Original failure patterns should still be present"

        verify_failures = [p for p in report.failure_patterns if "verify" in p.lower()]
        assert len(verify_failures) > 0, "Eval-based patterns should also be present"
