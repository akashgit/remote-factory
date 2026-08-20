"""Tests for OuterLoopReflector contrastive reflection."""

from __future__ import annotations

from pathlib import Path

from factory.cycle_analyzer import AgentStep, CycleRecord
from factory.outer_loop.reflector import OuterLoopReflector


def _make_record(
    score: float,
    steps: list[AgentStep] | None = None,
    kept: int = 0,
    reverted: int = 0,
    errored: int = 0,
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
        steps=steps or [],
        kept=kept,
        reverted=reverted,
        errored=errored,
    )


def _make_step(role: str, succeeded: bool = True, error: str | None = None, duration: float = 10.0) -> AgentStep:
    return AgentStep(
        order=0,
        role=role,
        started_at="2024-01-01T00:00:00",
        duration_s=duration,
        cost_usd=0.1,
        output_tokens=100,
        succeeded=succeeded,
        error=error,
    )


class TestOuterLoopReflector:
    def test_basic_reflection(self) -> None:
        reflector = OuterLoopReflector(k=1)

        records = [
            ("winner1", 0.9, _make_record(0.9, [_make_step("builder"), _make_step("researcher")], kept=2)),
            ("loser1", 0.1, _make_record(0.1, [_make_step("builder", succeeded=False, error="timeout")], errored=1)),
        ]

        report = reflector.reflect(records, generation=0)

        assert len(report.failure_patterns) > 0
        assert len(report.success_patterns) > 0
        assert report.top_k_ids == ["winner1"]
        assert report.bottom_k_ids == ["loser1"]

    def test_mutation_suggestions_from_role_diff(self) -> None:
        reflector = OuterLoopReflector(k=1)

        records = [
            ("w1", 0.8, _make_record(0.8, [_make_step("researcher"), _make_step("builder")], kept=1)),
            ("l1", 0.2, _make_record(0.2, [_make_step("builder")], reverted=1)),
        ]

        report = reflector.reflect(records, generation=0)

        role_suggestions = [s for s in report.mutation_suggestions if "researcher" in s.lower()]
        assert len(role_suggestions) > 0

    def test_insufficient_data(self) -> None:
        reflector = OuterLoopReflector(k=1)
        records = [("only1", 0.5, _make_record(0.5))]
        report = reflector.reflect(records, generation=0)

        assert len(report.failure_patterns) == 0
        assert len(report.success_patterns) == 0

    def test_none_records_filtered(self) -> None:
        reflector = OuterLoopReflector(k=1)

        records = [
            ("w1", 0.8, _make_record(0.8, [_make_step("builder")], kept=1)),
            ("n1", 0.5, None),
            ("l1", 0.2, _make_record(0.2, [_make_step("builder", succeeded=False)], errored=1)),
        ]

        report = reflector.reflect(records, generation=0)
        assert len(report.top_k_ids) == 1
        assert len(report.bottom_k_ids) == 1

    def test_save_report(self, tmp_path: Path) -> None:
        reflector = OuterLoopReflector(k=1, project_dir=tmp_path)

        records = [
            ("w1", 0.8, _make_record(0.8, [_make_step("builder")], kept=1)),
            ("l1", 0.2, _make_record(0.2, [], errored=1)),
        ]

        reflector.reflect(records, generation=3)

        json_path = tmp_path / ".factory" / "outer_loop" / "reflections" / "gen3.json"
        md_path = tmp_path / ".factory" / "outer_loop" / "reflections" / "gen3.md"
        assert json_path.exists()
        assert md_path.exists()

    def test_structural_recommendations_timeout(self) -> None:
        reflector = OuterLoopReflector(k=1)

        records = [
            ("w1", 0.9, _make_record(0.9, [_make_step("builder")], kept=1)),
            ("l1", 0.1, _make_record(0.1, [_make_step("builder", succeeded=False, duration=600.0)])),
        ]

        report = reflector.reflect(records, generation=0)
        timeout_recs = [r for r in report.structural_recommendations if "timeout" in r.lower()]
        assert len(timeout_recs) > 0

    def test_multiple_winners_losers(self) -> None:
        reflector = OuterLoopReflector(k=2)

        records = [
            ("w1", 0.9, _make_record(0.9, [_make_step("builder")], kept=2)),
            ("w2", 0.85, _make_record(0.85, [_make_step("builder"), _make_step("researcher")], kept=1)),
            ("l1", 0.2, _make_record(0.2, [], errored=1)),
            ("l2", 0.1, _make_record(0.1, [_make_step("builder", succeeded=False)], reverted=2)),
        ]

        report = reflector.reflect(records, generation=0)
        assert len(report.top_k_ids) == 2
        assert len(report.bottom_k_ids) == 2
