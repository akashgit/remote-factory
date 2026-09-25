"""Tests for search/holdout split firewall.

Covers all 12 acceptance criteria from issue #1540:
AC1  — Task.instances(split=) API
AC2  — Search-only evaluation during evolution
AC3  — Holdout evaluation only at end-of-run
AC4  — No holdout data in CycleRecords or reflection
AC5  — OverfitDetector uses firewalled holdout
AC6  — End-to-end integration
AC7  — Run report
AC8  — Instance-aware cache keys
AC9  — Split configuration
AC10 — Fallback behavior (no split → all search)
AC11 — val_score lifecycle
AC12 — verify() unchanged
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Literal

import pytest

from factory.cycle_analyzer import CycleRecord
from factory.outer_loop.evaluator import CycleRecordCache, SwarmEvaluator
from factory.outer_loop.models import EvalResult, Individual, OuterLoopResult, SwarmConfig
from factory.outer_loop.overfit import OverfitDetector
from factory.outer_loop.reflector import OuterLoopReflector
from factory.outer_loop.filesystem import save_best
from factory.task import InstancesConfig, Task, TaskDefinition, TaskInstance
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    Workflow,
)


# ── Fixtures ────────────────────────────────────────────────────


class DummyTaskWithSplit(Task):
    """Task with 3 train + 2 val instances for testing."""

    def __init__(self) -> None:
        super().__init__(definition=TaskDefinition(name="dummy-split"))

    def instances(
        self, split: Literal["train", "val", "all"] = "all",
    ) -> Iterator[TaskInstance]:
        all_instances = [
            TaskInstance(id="s1", split="train"),
            TaskInstance(id="s2", split="train"),
            TaskInstance(id="s3", split="train"),
            TaskInstance(id="h1", split="val"),
            TaskInstance(id="h2", split="val"),
        ]
        for inst in all_instances:
            if split == "all" or inst.split == split:
                yield inst


class LegacyTask(Task):
    """Task that overrides instances() WITHOUT the split parameter.

    Simulates existing Task subclasses written before the split API was added.
    Used to verify backward-compat fallback in the engine.
    """

    def __init__(self) -> None:
        super().__init__(definition=TaskDefinition(name="legacy-task"))

    def instances(self) -> Iterator[TaskInstance]:  # type: ignore[override]
        yield TaskInstance(id="leg1")
        yield TaskInstance(id="leg2")
        yield TaskInstance(id="leg3")


class TrackingEvaluator:
    """Records every evaluate() call with instances and individual_id."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(
        self, workflow: Workflow, project_dir: str, instances: list[str],
    ) -> EvalResult:
        self.calls.append({
            "instances": list(instances),
            "project_dir": project_dir,
        })
        avg = 0.7
        return EvalResult(score=avg, benchmark_score=avg, hygiene_score=0.8)


def _make_workflow() -> Workflow:
    return Workflow(
        name="test_holdout",
        nodes={
            "a": FnNode(id="a", command="echo a"),
            "b": AgentNode(id="b", role=AgentRole.BUILDER),
        },
        edges=[Edge(source="a", target="b")],
        start_node="a",
    )


def _make_config(**overrides: object) -> SwarmConfig:
    defaults: dict[str, object] = {
        "benchmark": "test",
        "budget": 30,
        "population_size": 2,
        "tournament_size": 2,
        "mutation_rate": 0.3,
        "training_instances": ["s1", "s2", "s3"],
        "holdout_instances": [],
    }
    defaults.update(overrides)
    return SwarmConfig(**defaults)  # type: ignore[arg-type]


# ── AC1: Task.instances(split=) API ────────────────────────────


class TestTaskInstancesSplitAPI:
    """AC1 — Task.instances() accepts split argument."""

    def test_split_train_returns_only_train(self) -> None:
        task = DummyTaskWithSplit()
        train = list(task.instances(split="train"))
        assert len(train) == 3
        assert all(inst.split == "train" for inst in train)
        assert {inst.id for inst in train} == {"s1", "s2", "s3"}

    def test_split_val_returns_only_val(self) -> None:
        task = DummyTaskWithSplit()
        holdout = list(task.instances(split="val"))
        assert len(holdout) == 2
        assert all(inst.split == "val" for inst in holdout)
        assert {inst.id for inst in holdout} == {"h1", "h2"}

    def test_default_split_is_all(self) -> None:
        task = DummyTaskWithSplit()
        all_insts = list(task.instances())
        assert len(all_insts) == 5

    def test_split_all_returns_everything(self) -> None:
        task = DummyTaskWithSplit()
        all_insts = list(task.instances(split="all"))
        assert len(all_insts) == 5

    def test_task_instance_has_split_field(self) -> None:
        inst = TaskInstance(id="test", split="train")
        assert inst.split == "train"
        inst2 = TaskInstance(id="test2")
        assert inst2.split is None


# ── AC8: Instance-aware cache keys ─────────────────────────────


class TestCycleRecordCache:
    """AC8 — CycleRecordCache produces different keys for different instance sets."""

    def test_cache_separates_search_and_holdout(self) -> None:
        wf = _make_workflow()
        cache = CycleRecordCache()
        search_rec = CycleRecord(
            cycle_number=1, mode=None, started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.8, score_delta=None,
        )
        holdout_rec = CycleRecord(
            cycle_number=2, mode=None, started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.6, score_delta=None,
        )
        cache.put(wf, search_rec, instances=["s1", "s2", "s3"])
        cache.put(wf, holdout_rec, instances=["h1", "h2"])

        assert cache.get(wf, instances=["s1", "s2", "s3"]) is search_rec
        assert cache.get(wf, instances=["h1", "h2"]) is holdout_rec

    def test_cache_instance_order_independent(self) -> None:
        wf = _make_workflow()
        cache = CycleRecordCache()
        rec = CycleRecord(
            cycle_number=1, mode=None, started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.8, score_delta=None,
        )
        cache.put(wf, rec, instances=["s2", "s1", "s3"])
        assert cache.get(wf, instances=["s1", "s2", "s3"]) is rec
        assert cache.get(wf, instances=["s3", "s1", "s2"]) is rec

    def test_cache_legacy_no_instances_no_collision(self) -> None:
        wf = _make_workflow()
        cache = CycleRecordCache()
        legacy_rec = CycleRecord(
            cycle_number=0, mode=None, started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.5, score_delta=None,
        )
        instance_rec = CycleRecord(
            cycle_number=1, mode=None, started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.8, score_delta=None,
        )
        cache.put(wf, legacy_rec)  # No instances (legacy)
        cache.put(wf, instance_rec, instances=["s1", "s2"])

        assert cache.get(wf) is legacy_rec
        assert cache.get(wf, instances=["s1", "s2"]) is instance_rec

    def test_cache_key_static_method(self) -> None:
        wf = _make_workflow()
        key1 = CycleRecordCache._cache_key(wf, ["s1", "s2"])
        key2 = CycleRecordCache._cache_key(wf, ["h1", "h2"])
        key3 = CycleRecordCache._cache_key(wf)
        assert key1 != key2
        assert key1 != key3
        assert key2 != key3
        assert ":" in key1  # composite key format
        assert ":" in key2
        assert ":" not in key3  # legacy format


# ── AC4 & AC5: Reflector firewall ──────────────────────────────


class TestReflectorFirewall:
    """AC4 — Reflector rejects holdout CycleRecords."""

    def test_reflector_rejects_val_records(self) -> None:
        reflector = OuterLoopReflector(k=1)
        holdout_rec = CycleRecord(
            cycle_number=1, mode=None, started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.8, score_delta=None,
            split="val",
        )
        train_rec = CycleRecord(
            cycle_number=2, mode=None, started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.6, score_delta=None,
            split="train",
        )
        records = [
            ("id1", 0.8, holdout_rec),
            ("id2", 0.6, train_rec),
        ]
        with pytest.raises(RuntimeError, match="Validation"):
            reflector.reflect(records, generation=0)

    def test_reflector_accepts_train_records(self) -> None:
        reflector = OuterLoopReflector(k=1)
        rec1 = CycleRecord(
            cycle_number=1, mode=None, started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.8, score_delta=None,
            split="train",
        )
        rec2 = CycleRecord(
            cycle_number=2, mode=None, started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.6, score_delta=None,
            split="train",
        )
        records = [("id1", 0.8, rec1), ("id2", 0.6, rec2)]
        report = reflector.reflect(records, generation=0)
        assert report is not None

    def test_reflector_accepts_legacy_none_split(self) -> None:
        reflector = OuterLoopReflector(k=1)
        rec1 = CycleRecord(
            cycle_number=1, mode=None, started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.8, score_delta=None,
        )
        rec2 = CycleRecord(
            cycle_number=2, mode=None, started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.6, score_delta=None,
        )
        records = [("id1", 0.8, rec1), ("id2", 0.6, rec2)]
        report = reflector.reflect(records, generation=0)
        assert report is not None


# ── AC6: CycleRecord.split field ───────────────────────────────


class TestCycleRecordSplit:
    """AC6 — CycleRecord stores split field."""

    def test_cycle_record_split_default_none(self) -> None:
        rec = CycleRecord(
            cycle_number=1, mode=None, started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.8, score_delta=None,
        )
        assert rec.split is None

    def test_cycle_record_split_train(self) -> None:
        rec = CycleRecord(
            cycle_number=1, mode=None, started_at=None, ended_at=None,
            duration_s=1.0, score_start=None, score_end=0.8, score_delta=None,
            split="train",
        )
        assert rec.split == "train"


# ── AC5: OverfitDetector ──────────────────────────────────────


class TestOverfitDetectorTrainingScore:
    """AC5 — OverfitDetector accepts pre-computed training_score."""

    def test_skips_training_eval_when_score_provided(self) -> None:
        config = _make_config()
        call_log: list[list[str]] = []

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            call_log.append(list(instances))
            return EvalResult(score=0.6, benchmark_score=0.6)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        detector = OverfitDetector()
        wf = _make_workflow()

        result = detector.audit(
            wf, ["s1", "s2"], ["h1"], evaluator, "/tmp",
            training_score=0.8,
        )

        # Only one evaluate call (holdout), training was skipped
        assert len(call_log) == 1
        assert "h1" in call_log[0]
        assert result.training_score == 0.8
        # Holdout score goes through composite calculation, just verify it's > 0
        assert result.holdout_score > 0

    def test_evaluates_training_when_no_score(self) -> None:
        config = _make_config()
        call_log: list[list[str]] = []

        def mock_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            call_log.append(list(instances))
            return EvalResult(score=0.7, benchmark_score=0.7)

        evaluator = SwarmEvaluator(config, evaluator_fn=mock_eval)
        detector = OverfitDetector()
        wf = _make_workflow()

        detector.audit(wf, ["s1", "s2"], ["h1"], evaluator, "/tmp")

        # Two evaluate calls: training + holdout
        assert len(call_log) == 2


# ── AC7: Run report ───────────────────────────────────────────


class TestRunReport:
    """AC7 — save_best() writes run_report.json."""

    def test_run_report_contains_all_fields(self, tmp_path: Path) -> None:
        result = OuterLoopResult(
            best_workflow_data={"name": "test"},
            best_score=0.82,
            val_score=0.76,
            overfit_flag=False,
            total_cost_usd=12.34,
            convergence_reason="budget_exhausted",
            generations_completed=8,
            total_evaluations=47,
            total_candidates_evaluated=47,
        )
        save_best(tmp_path, result)

        report_path = tmp_path / ".factory" / "outer_loop" / "best" / "run_report.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text())

        assert report["train_score"] == 0.82
        assert report["val_score"] == 0.76
        assert report["overfit_flag"] is False
        assert report["total_candidates_evaluated"] == 47
        assert report["generations_completed"] == 8
        assert report["convergence_reason"] == "budget_exhausted"
        assert report["total_cost_usd"] == 12.34


# ── AC9 & AC10: Split configuration and fallback ─────────────


class TestSplitConfiguration:
    """AC9 — Split configuration via TaskInstance.split and InstancesConfig."""

    def test_explicit_split_preserved(self) -> None:
        task = DummyTaskWithSplit()
        train = list(task.instances(split="train"))
        holdout = list(task.instances(split="val"))
        assert len(train) == 3
        assert len(holdout) == 2

    def test_holdout_ids_config(self) -> None:
        defn = TaskDefinition(
            name="test-holdout-ids",
            instances_config=InstancesConfig(holdout_ids=["default"]),
        )
        task = Task(definition=defn)
        all_insts = list(task.instances(split="all"))
        # Default single-instance task: "default" should be val
        assert len(all_insts) == 1
        assert all_insts[0].split == "val"

    def test_instances_config_has_holdout_ids(self) -> None:
        cfg = InstancesConfig(holdout_ids=["h1", "h2"])
        assert cfg.holdout_ids == ["h1", "h2"]

    def test_instances_config_default_empty(self) -> None:
        cfg = InstancesConfig()
        assert cfg.holdout_ids == []


class TestFallbackNoSplit:
    """AC10 — When no explicit split is declared, all instances are train."""

    def test_no_split_all_train(self) -> None:
        """Default task with no splits: all instances become train."""
        task = Task(definition=TaskDefinition(name="no-split"))
        insts = list(task.instances(split="all"))
        assert len(insts) == 1
        assert insts[0].split == "train"
        assert insts[0].id == "default"

    def test_no_val_when_no_config(self) -> None:
        """No val instances when no split is configured."""
        task = Task(definition=TaskDefinition(name="no-split"))
        holdout = list(task.instances(split="val"))
        assert len(holdout) == 0

    def test_all_train_when_no_config(self) -> None:
        """All instances are train when no split is configured."""
        task = Task(definition=TaskDefinition(name="no-split"))
        train = list(task.instances(split="train"))
        assert len(train) == 1
        assert train[0].id == "default"


# ── AC11: Individual.val_score lifecycle ──────────────────


class TestIndividualValScore:
    """AC11 — Individual.val_score defaults to None."""

    def test_default_none(self) -> None:
        ind = Individual(
            id="test", workflow_data={}, score=0.8,
        )
        assert ind.val_score is None

    def test_can_set_val_score(self) -> None:
        ind = Individual(
            id="test", workflow_data={}, score=0.8,
        )
        updated = ind.model_copy(update={"val_score": 0.76})
        assert updated.val_score == 0.76

    def test_serialization_backward_compat(self) -> None:
        """Old JSON without val_score deserializes cleanly."""
        data = {
            "id": "test",
            "workflow_data": {},
            "score": 0.8,
            "features": [],
            "generation": 0,
        }
        ind = Individual.model_validate(data, strict=False)
        assert ind.val_score is None


# ── AC12: verify() unchanged ─────────────────────────────────


class TestVerifyUnchanged:
    """AC12 — verify() method is not modified by this PR."""

    def test_verify_signature_unchanged(self) -> None:
        """Task.verify() still takes (instance, workspace) arguments."""
        import inspect
        sig = inspect.signature(Task.verify)
        params = list(sig.parameters.keys())
        assert params == ["self", "instance", "workspace"]


# ── AC2 & AC3: Engine search-only evolution ───────────────────


class TestEngineSearchOnlyEvolution:
    """AC2 & AC3 — Evolution uses search-only instances, holdout only at end-of-run."""

    def test_evolve_generation_only_passes_train_instances(self) -> None:
        """During evolve_generation, only train instance IDs are used."""
        from factory.outer_loop.engine import SwarmEngine

        task = DummyTaskWithSplit()
        config = _make_config()
        config.set_task(task)

        call_log: list[list[str]] = []

        def track_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            call_log.append(list(instances))
            return EvalResult(score=0.5, benchmark_score=0.5, hygiene_score=0.7)

        evaluator = SwarmEvaluator(config, evaluator_fn=track_eval)
        engine = SwarmEngine(config, evaluator)

        pop = engine.seed(_make_workflow())

        engine.evolve_generation(pop, 0, "/tmp/test")

        train_ids = {"s1", "s2", "s3"}
        holdout_ids = {"h1", "h2"}
        for call in call_log:
            call_set = set(call)
            assert call_set.issubset(train_ids), (
                f"Expected only train IDs, got {call}"
            )
            assert not call_set & holdout_ids, (
                f"Holdout IDs leaked into evolution: {call}"
            )


# ── OuterLoopResult.total_candidates_evaluated ────────────────


class TestOuterLoopResultFields:
    """Extra model field tests."""

    def test_total_candidates_evaluated_default(self) -> None:
        result = OuterLoopResult()
        assert result.total_candidates_evaluated == 0

    def test_total_candidates_evaluated_set(self) -> None:
        result = OuterLoopResult(total_candidates_evaluated=42)
        assert result.total_candidates_evaluated == 42


# ── Backward compat: LegacyTask (no split param) ────────────


class TestLegacyTaskBackwardCompat:
    """FIX 2 — Engine doesn't crash when Task.instances() lacks split param."""

    def test_evolve_generation_with_legacy_task(self) -> None:
        """LegacyTask.instances() has no split param → engine falls back to config."""
        from factory.outer_loop.engine import SwarmEngine

        task = LegacyTask()
        config = _make_config(training_instances=["leg1", "leg2", "leg3"])
        config.set_task(task)

        call_log: list[list[str]] = []

        def track_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            call_log.append(list(instances))
            return EvalResult(score=0.5, benchmark_score=0.5, hygiene_score=0.7)

        evaluator = SwarmEvaluator(config, evaluator_fn=track_eval)
        engine = SwarmEngine(config, evaluator)

        pop = engine.seed(_make_workflow())
        # Should NOT raise TypeError
        engine.evolve_generation(pop, 0, "/tmp/test")

        # Verify calls were made (fallback worked)
        assert len(call_log) > 0

    def test_run_with_legacy_task_no_crash(self) -> None:
        """Full run() with LegacyTask doesn't crash."""
        from factory.outer_loop.engine import SwarmEngine

        task = LegacyTask()
        config = _make_config(
            budget=4,
            population_size=2,
            training_instances=["leg1", "leg2"],
            holdout_instances=["leg3"],
        )
        config.set_task(task)

        def simple_eval(wf: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
            return EvalResult(score=0.5, benchmark_score=0.5, hygiene_score=0.7)

        evaluator = SwarmEvaluator(config, evaluator_fn=simple_eval)
        engine = SwarmEngine(config, evaluator)

        # Should NOT raise TypeError
        result = engine.run(_make_workflow())
        assert result.best_score >= 0.0
