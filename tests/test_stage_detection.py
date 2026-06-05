"""Tests for stage terminal condition evaluation and status models."""

from __future__ import annotations

from factory.build_root.stage import (
    PipelineStatus,
    StageState,
    StageStatus,
    build_default_pipeline,
    evaluate_terminal_condition,
)


class TestTerminalConditions:
    def test_stage1_terminal_zero_failures(self) -> None:
        assert evaluate_terminal_condition(1, {"failed": 0, "resolved": 50})

    def test_stage1_not_terminal_with_failures(self) -> None:
        assert not evaluate_terminal_condition(1, {"failed": 3, "resolved": 47})

    def test_stage2_terminal_all_recovered(self) -> None:
        assert evaluate_terminal_condition(
            2, {"recoverable": 5, "recovered": 5, "dead_ends": 0}
        )

    def test_stage2_terminal_mix_recovered_dead_ends(self) -> None:
        assert evaluate_terminal_condition(
            2, {"recoverable": 5, "recovered": 3, "dead_ends": 2}
        )

    def test_stage2_not_terminal_outstanding(self) -> None:
        assert not evaluate_terminal_condition(
            2, {"recoverable": 5, "recovered": 2, "dead_ends": 1}
        )

    def test_stage3_terminal_zero_failures(self) -> None:
        assert evaluate_terminal_condition(3, {"failed": 0, "passed": 10})

    def test_stage3_not_terminal_with_failures(self) -> None:
        assert not evaluate_terminal_condition(3, {"failed": 2, "passed": 8})

    def test_stage4_terminal_zero_failures(self) -> None:
        assert evaluate_terminal_condition(4, {"failed": 0, "passed": 100})

    def test_stage4_not_terminal_with_failures(self) -> None:
        assert not evaluate_terminal_condition(4, {"failed": 5, "passed": 95})

    def test_unknown_stage_returns_false(self) -> None:
        assert not evaluate_terminal_condition(99, {"failed": 0})


class TestStageStatusModel:
    def test_default_values(self) -> None:
        s = StageStatus(stage=1, name="DEP RESOLVE")
        assert s.state == StageState.PENDING
        assert s.cycles == 0
        assert s.metric_current == 0
        assert s.metric_total == 0
        assert s.elapsed_seconds == 0.0

    def test_all_values(self) -> None:
        s = StageStatus(
            stage=3,
            name="COMPILE",
            state=StageState.ACTIVE,
            cycles=5,
            metric_current=8,
            metric_total=10,
            elapsed_seconds=120.5,
        )
        assert s.stage == 3
        assert s.state == StageState.ACTIVE

    def test_json_roundtrip(self) -> None:
        s = StageStatus(stage=2, name="ARTIFACT RECOVERY", state=StageState.COMPLETED)
        data = s.model_dump()
        s2 = StageStatus(**data)
        assert s == s2

    def test_extra_forbid(self) -> None:
        import pytest

        with pytest.raises(Exception):
            StageStatus(stage=1, name="X", unknown_field="bad")


class TestPipelineStatus:
    def test_default_pipeline(self) -> None:
        p = build_default_pipeline()
        assert len(p.stages) == 4
        assert p.stage_completed == 0
        assert all(s.state == StageState.PENDING for s in p.stages)

    def test_stage_names(self) -> None:
        p = build_default_pipeline()
        names = [s.name for s in p.stages]
        assert names == ["DEP RESOLVE", "ARTIFACT RECOVERY", "COMPILE", "TEST"]

    def test_pipeline_json_roundtrip(self) -> None:
        p = build_default_pipeline()
        data = p.model_dump()
        p2 = PipelineStatus(**data)
        assert p == p2

    def test_completed_pipeline(self) -> None:
        p = PipelineStatus(
            stages=[
                StageStatus(stage=i, name=n, state=StageState.COMPLETED)
                for i, n in [(1, "DEP RESOLVE"), (2, "ARTIFACT RECOVERY"), (3, "COMPILE"), (4, "TEST")]
            ],
            stage_completed=4,
        )
        assert p.stage_completed == 4
        assert all(s.state == StageState.COMPLETED for s in p.stages)
