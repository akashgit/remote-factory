"""E2E test: VerifyResult.details flows through the entire pipeline.

Task.verify() → adapter → EvalResult → CycleRecord → reflector → mutation suggestions.
"""

from __future__ import annotations

from factory.cycle_analyzer import AgentStep, CycleRecord
from factory.outer_loop.reflector import OuterLoopReflector
from factory.outer_loop.verify_adapter import eval_result_from_verify_results
from factory.task import VerifyResult


def _make_agent_step(role: str, succeeded: bool = True) -> AgentStep:
    return AgentStep(
        order=0,
        role=role,
        started_at="2026-01-01T00:00:00",
        duration_s=10.0,
        cost_usd=0.01,
        output_tokens=100,
        succeeded=succeeded,
    )


def _build_cycle_record(
    *,
    score: float,
    verify_results: list[VerifyResult],
    steps: list[AgentStep] | None = None,
    extra_details: dict | None = None,
) -> CycleRecord:
    """Build a CycleRecord that mirrors what the evaluator produces."""
    adapted = eval_result_from_verify_results(verify_results)
    details: dict[str, object] = {}
    details["verify"] = adapted.details
    if extra_details:
        details.update(extra_details)

    return CycleRecord(
        cycle_number=1,
        mode="evolve",
        started_at="2026-01-01T00:00:00",
        ended_at="2026-01-01T00:10:00",
        duration_s=600.0,
        score_start=0.0,
        score_end=score,
        score_delta=score,
        steps=steps or [_make_agent_step("builder")],
        eval_details=details,
    )


class TestE2EVerifyPipeline:
    """Proves VerifyResult.details flows end-to-end into reflector output."""

    def test_scoring_contract_details_flow_through(self) -> None:
        """scoring_contract / returncode details survive the full pipeline."""
        passing = [
            VerifyResult(
                passed=True,
                score=1.0,
                details={"scoring_contract": "json", "returncode": 0},
            ),
            VerifyResult(
                passed=True,
                score=1.0,
                details={"scoring_contract": "json", "returncode": 0},
            ),
            VerifyResult(
                passed=True,
                score=0.8,
                details={"scoring_contract": "json", "returncode": 0},
            ),
        ]
        failing = [
            VerifyResult(
                passed=False,
                score=0.0,
                details={
                    "scoring_contract": "json",
                    "returncode": 1,
                    "stdout": "error output",
                    "stderr": "Traceback (most recent call last):\n  ...",
                },
            ),
            VerifyResult(
                passed=False,
                score=0.0,
                details={
                    "scoring_contract": "json",
                    "returncode": 2,
                    "stdout": "",
                    "stderr": "AssertionError: expected 42 got 0",
                },
            ),
        ]

        winner_record = _build_cycle_record(
            score=0.9,
            verify_results=passing,
            steps=[_make_agent_step("builder"), _make_agent_step("qa")],
        )
        loser_record = _build_cycle_record(
            score=0.1,
            verify_results=failing,
            steps=[_make_agent_step("builder", succeeded=False)],
        )

        reflector = OuterLoopReflector(k=1)
        report = reflector.reflect([
            ("winner-id-1234", 0.9, winner_record),
            ("loser-id-5678", 0.1, loser_record),
        ])

        failure_text = "\n".join(report.failure_patterns)
        assert "returncode" in failure_text, (
            f"Expected returncode in failure_patterns, got: {report.failure_patterns}"
        )
        assert "loser-id" in failure_text

        assert any("verify" in p.lower() or "passed" in p.lower() for p in report.success_patterns), (
            f"Expected verify pass info in success_patterns, got: {report.success_patterns}"
        )

        assert len(report.mutation_suggestions) > 0, "Expected at least one mutation suggestion"
        assert any(
            "verify" in s.lower() or "test" in s.lower() or "NODE" in s
            for s in report.mutation_suggestions
        ), f"Expected eval-informed suggestions, got: {report.mutation_suggestions}"

    def test_mixed_pass_fail_verify_results(self) -> None:
        """Winner mostly passes, loser mostly fails — reflector picks this up."""
        winner_vrs = [
            VerifyResult(passed=True, score=1.0, details={"returncode": 0}),
            VerifyResult(passed=True, score=1.0, details={"returncode": 0}),
            VerifyResult(passed=True, score=0.9, details={"returncode": 0}),
            VerifyResult(passed=False, score=0.0, details={"returncode": 1}),
        ]
        loser_vrs = [
            VerifyResult(passed=False, score=0.0, details={"returncode": 1}),
            VerifyResult(passed=True, score=1.0, details={"returncode": 0}),
            VerifyResult(passed=False, score=0.0, details={"returncode": 1}),
            VerifyResult(passed=False, score=0.0, details={"returncode": 1}),
        ]

        winner_rec = _build_cycle_record(score=0.8, verify_results=winner_vrs)
        loser_rec = _build_cycle_record(score=0.2, verify_results=loser_vrs)

        reflector = OuterLoopReflector(k=1)
        report = reflector.reflect([
            ("win-aaaa1111", 0.8, winner_rec),
            ("lose-bbbb2222", 0.2, loser_rec),
        ])

        failure_text = "\n".join(report.failure_patterns)
        assert "3/4" in failure_text or "failed" in failure_text.lower(), (
            f"Expected failure count in patterns, got: {report.failure_patterns}"
        )

        success_text = "\n".join(report.success_patterns)
        assert "3/4" in success_text or "passed" in success_text.lower(), (
            f"Expected pass count in patterns, got: {report.success_patterns}"
        )

    def test_empty_details_no_crash(self) -> None:
        """VerifyResults with empty details don't crash the pipeline."""
        vrs_no_details = [
            VerifyResult(passed=True, score=1.0),
            VerifyResult(passed=False, score=0.0),
        ]
        vrs_empty_dict = [
            VerifyResult(passed=True, score=0.5, details={}),
            VerifyResult(passed=False, score=0.0, details={}),
        ]

        rec_a = _build_cycle_record(score=0.5, verify_results=vrs_no_details)
        rec_b = _build_cycle_record(score=0.3, verify_results=vrs_empty_dict)

        reflector = OuterLoopReflector(k=1)
        report = reflector.reflect([
            ("id-no-details", 0.5, rec_a),
            ("id-empty-dict", 0.3, rec_b),
        ])
        assert isinstance(report.failure_patterns, list)
        assert isinstance(report.mutation_suggestions, list)

    def test_none_eval_details_no_crash(self) -> None:
        """CycleRecord with eval_details=None doesn't crash the reflector."""
        rec_with = _build_cycle_record(
            score=0.8,
            verify_results=[VerifyResult(passed=True, score=1.0, details={"returncode": 0})],
        )
        rec_without = CycleRecord(
            cycle_number=1,
            mode="evolve",
            started_at="2026-01-01T00:00:00",
            ended_at="2026-01-01T00:10:00",
            duration_s=600.0,
            score_start=0.0,
            score_end=0.2,
            score_delta=0.2,
            steps=[_make_agent_step("builder")],
            eval_details=None,
        )

        reflector = OuterLoopReflector(k=1)
        report = reflector.reflect([
            ("has-details", 0.8, rec_with),
            ("no-details", 0.2, rec_without),
        ])
        assert isinstance(report.failure_patterns, list)

    def test_chess_evolve_details_flow_through(self) -> None:
        """Domain-specific details (blunder_count, avg_eval) flow through."""
        chess_passing = [
            VerifyResult(
                passed=True,
                score=0.9,
                details={
                    "blunder_count": 0,
                    "avg_eval": 2.5,
                    "game_result": "win",
                    "returncode": 0,
                },
            ),
            VerifyResult(
                passed=True,
                score=0.7,
                details={
                    "blunder_count": 1,
                    "avg_eval": 1.2,
                    "game_result": "draw",
                    "returncode": 0,
                },
            ),
        ]
        chess_failing = [
            VerifyResult(
                passed=False,
                score=0.1,
                details={
                    "blunder_count": 5,
                    "avg_eval": -3.0,
                    "game_result": "loss",
                    "returncode": 1,
                },
            ),
            VerifyResult(
                passed=False,
                score=0.0,
                details={
                    "blunder_count": 8,
                    "avg_eval": -5.0,
                    "game_result": "loss",
                    "returncode": 1,
                },
            ),
        ]

        winner_rec = _build_cycle_record(score=0.8, verify_results=chess_passing)
        loser_rec = _build_cycle_record(score=0.05, verify_results=chess_failing)

        adapted_winner = eval_result_from_verify_results(chess_passing)
        winner_instances = adapted_winner.details["instance_results"]
        assert winner_instances[0]["details"]["blunder_count"] == 0
        assert winner_instances[0]["details"]["avg_eval"] == 2.5

        adapted_loser = eval_result_from_verify_results(chess_failing)
        loser_instances = adapted_loser.details["instance_results"]
        assert loser_instances[0]["details"]["blunder_count"] == 5
        assert loser_instances[0]["details"]["avg_eval"] == -3.0

        reflector = OuterLoopReflector(k=1)
        report = reflector.reflect([
            ("chess-winner-1", 0.8, winner_rec),
            ("chess-loser-2", 0.05, loser_rec),
        ])

        failure_text = "\n".join(report.failure_patterns)
        assert "returncode" in failure_text
        assert "chess-lo" in failure_text

        assert len(report.mutation_suggestions) > 0

    def test_adapter_preserves_all_detail_fields(self) -> None:
        """Adapter faithfully preserves arbitrary detail keys."""
        vr = VerifyResult(
            passed=True,
            score=0.95,
            details={
                "scoring_contract": "json",
                "returncode": 0,
                "custom_metric": 42,
                "nested": {"a": 1, "b": [2, 3]},
            },
        )
        result = eval_result_from_verify_results([vr])
        inst = result.details["instance_results"][0]
        assert inst["details"]["custom_metric"] == 42
        assert inst["details"]["nested"] == {"a": 1, "b": [2, 3]}

    def test_verify_score_comparison_drives_mutation_suggestion(self) -> None:
        """When top-K and bottom-K have different avg verify scores,
        the reflector produces a verify-focused mutation suggestion."""
        top_vrs = [
            VerifyResult(passed=True, score=1.0, details={"returncode": 0}),
            VerifyResult(passed=True, score=1.0, details={"returncode": 0}),
        ]
        bottom_vrs = [
            VerifyResult(passed=False, score=0.0, details={"returncode": 1}),
            VerifyResult(passed=False, score=0.0, details={"returncode": 1}),
        ]

        top_rec = _build_cycle_record(score=1.0, verify_results=top_vrs)
        bottom_rec = _build_cycle_record(score=0.0, verify_results=bottom_vrs)

        reflector = OuterLoopReflector(k=1)
        report = reflector.reflect([
            ("top-1111", 1.0, top_rec),
            ("bottom-2222", 0.0, bottom_rec),
        ])

        suggestions_text = "\n".join(report.mutation_suggestions)
        assert "verify" in suggestions_text.lower(), (
            f"Expected verify-focused suggestion, got: {report.mutation_suggestions}"
        )

    def test_full_pipeline_four_individuals(self) -> None:
        """Reflector with k=2, four individuals — two winners, two losers."""
        def make_vrs(pass_count: int, fail_count: int) -> list[VerifyResult]:
            vrs = []
            for _ in range(pass_count):
                vrs.append(VerifyResult(passed=True, score=1.0, details={"returncode": 0}))
            for _ in range(fail_count):
                vrs.append(VerifyResult(passed=False, score=0.0, details={"returncode": 1}))
            return vrs

        records: list[tuple[str, float, CycleRecord | None]] = [
            ("top-1", 0.95, _build_cycle_record(
                score=0.95,
                verify_results=make_vrs(5, 0),
                steps=[_make_agent_step("builder"), _make_agent_step("qa")],
            )),
            ("top-2", 0.85, _build_cycle_record(
                score=0.85,
                verify_results=make_vrs(4, 1),
                steps=[_make_agent_step("builder"), _make_agent_step("qa")],
            )),
            ("bottom-1", 0.15, _build_cycle_record(
                score=0.15,
                verify_results=make_vrs(1, 4),
                steps=[_make_agent_step("builder")],
            )),
            ("bottom-2", 0.05, _build_cycle_record(
                score=0.05,
                verify_results=make_vrs(0, 5),
                steps=[_make_agent_step("builder")],
            )),
        ]

        reflector = OuterLoopReflector(k=2)
        report = reflector.reflect(records)

        assert "top-1" in report.top_k_ids[0]
        assert "top-2" in report.top_k_ids[1]
        assert "bottom-2" in report.bottom_k_ids[0] or "bottom-1" in report.bottom_k_ids[0]

        failure_text = "\n".join(report.failure_patterns)
        assert "5/5" in failure_text or "4/5" in failure_text, (
            f"Expected instance failure counts, got: {report.failure_patterns}"
        )

        assert any("qa" in s.lower() for s in report.mutation_suggestions), (
            f"Expected qa-related suggestion (present in winners not losers), "
            f"got: {report.mutation_suggestions}"
        )

        suggestions_text = "\n".join(report.mutation_suggestions)
        assert "verify" in suggestions_text.lower() or "NODE" in suggestions_text
