"""Integration tests for inner/outer loop infrastructure.

Covers:
- factory.md -> config.json round-trip with Multi-Run and Surface Scoping
- execute_multi_run() with deterministic commands and all aggregation methods
- detect_plateau() with various history shapes
- CheckpointState with new plateau_count and loop_level fields
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from factory.checkpoint import (
    CheckpointState,
    format_checkpoint,
    load_checkpoint,
    save_checkpoint,
)
from factory.models import (
    AggregateMethod,
    ExperimentRecord,
    InnerLoopConfig,
)
from factory.research.runner import execute_multi_run
from factory.store import ExperimentStore
from factory.strategy import detect_plateau


# ── helpers ─────────────────────────────────────────────────────


def _make_record(
    exp_id: int,
    score_after: float | None,
    verdict: str = "keep",
) -> ExperimentRecord:
    """Create a minimal ExperimentRecord for plateau detection tests."""
    return ExperimentRecord(
        id=exp_id,
        timestamp=datetime(2026, 1, 1, 0, 0, 0),
        hypothesis=f"H{exp_id}",
        change_summary="",
        issue_number=None,
        pr_number=None,
        score_before=None,
        score_after=score_after,
        delta=None,
        verdict=verdict,
        cost_usd=None,
        notes="",
    )


# ── config round-trip ───────────────────────────────────────────


class TestConfigRoundTrip:
    """factory.md -> reparse_config -> config.json -> read_config round-trip."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> ExperimentStore:
        project = tmp_path / "project"
        project.mkdir()
        return ExperimentStore(project)

    async def test_roundtrip_with_multi_run_and_surface_scoping(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nResearch loop test\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n- no deletes\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n- small changes\n\n"
            "## Multi-Run\n"
            "- Runs per cycle: 5\n"
            "- Aggregate: median\n"
            "- Max runs per cycle: 10\n\n"
            "## Surface Scoping\n"
            "- Plateau threshold: 4\n"
            "- Max escalation cycles: 8\n"
            "- Inner surfaces: src/model.py, src/train.py\n"
            "- Outer surfaces: config/arch.yaml\n"
        )
        store.factory_dir.mkdir(exist_ok=True)

        # Step 1: Parse factory.md -> config.json
        config = await store.reparse_config()

        # Step 2: Verify inner_loop
        assert config.inner_loop is not None
        assert config.inner_loop.runs_per_cycle == 5
        assert config.inner_loop.aggregate == AggregateMethod.median
        assert config.inner_loop.max_runs_per_cycle == 10

        # Step 3: Verify outer_loop
        assert config.outer_loop is not None
        assert config.outer_loop.plateau_threshold == 4
        assert config.outer_loop.max_escalation_cycles == 8
        assert config.outer_loop.inner_surfaces == ["src/model.py", "src/train.py"]
        assert config.outer_loop.outer_surfaces == ["config/arch.yaml"]

        # Step 4: Read back from config.json
        loaded = await store.read_config()
        assert loaded == config
        assert loaded.inner_loop is not None
        assert loaded.inner_loop.aggregate == AggregateMethod.median
        assert loaded.outer_loop is not None
        assert loaded.outer_loop.plateau_threshold == 4

    async def test_roundtrip_without_loops_backward_compat(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nNormal project\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n\n"
        )
        store.factory_dir.mkdir(exist_ok=True)

        config = await store.reparse_config()
        assert config.inner_loop is None
        assert config.outer_loop is None

        loaded = await store.read_config()
        assert loaded.inner_loop is None
        assert loaded.outer_loop is None

    async def test_config_json_contains_loop_fields(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nTest\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n\n"
            "## Multi-Run\n"
            "- Runs per cycle: 3\n"
            "- Aggregate: all_pass\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        await store.reparse_config()

        raw = json.loads((store.factory_dir / "config.json").read_text())
        assert "inner_loop" in raw
        assert raw["inner_loop"]["runs_per_cycle"] == 3
        assert raw["inner_loop"]["aggregate"] == "all_pass"
        assert raw["outer_loop"] is None


# ── execute_multi_run ───────────────────────────────────────────


class TestExecuteMultiRun:
    async def test_mean_aggregation(self) -> None:
        config = InnerLoopConfig(runs_per_cycle=3, aggregate=AggregateMethod.mean)
        # echo scores: 0.8, 0.8, 0.8 -> mean = 0.8
        score = await execute_multi_run("echo 0.8", config)
        assert score == pytest.approx(0.8)

    async def test_median_aggregation(self) -> None:
        config = InnerLoopConfig(runs_per_cycle=3, aggregate=AggregateMethod.median)
        score = await execute_multi_run("echo 0.5", config)
        assert score == pytest.approx(0.5)

    async def test_max_aggregation(self) -> None:
        config = InnerLoopConfig(runs_per_cycle=3, aggregate=AggregateMethod.max)
        score = await execute_multi_run("echo 0.9", config)
        assert score == pytest.approx(0.9)

    async def test_all_pass_success(self) -> None:
        config = InnerLoopConfig(runs_per_cycle=3, aggregate=AggregateMethod.all_pass)
        score = await execute_multi_run("echo 1.0", config)
        assert score == 1.0

    async def test_all_pass_failure(self) -> None:
        config = InnerLoopConfig(runs_per_cycle=3, aggregate=AggregateMethod.all_pass)
        score = await execute_multi_run("exit 1", config)
        assert score == 0.0

    async def test_single_run(self) -> None:
        config = InnerLoopConfig(runs_per_cycle=1, aggregate=AggregateMethod.mean)
        score = await execute_multi_run("echo 0.95", config)
        assert score == pytest.approx(0.95)

    async def test_nonzero_exit_contributes_zero(self) -> None:
        """Non-zero exits produce 0.0 scores for mean/median/max."""
        config = InnerLoopConfig(runs_per_cycle=1, aggregate=AggregateMethod.mean)
        score = await execute_multi_run("exit 1", config)
        assert score == 0.0

    async def test_unparseable_output(self) -> None:
        """Non-numeric stdout produces 0.0 score."""
        config = InnerLoopConfig(runs_per_cycle=1, aggregate=AggregateMethod.mean)
        score = await execute_multi_run("echo not_a_number", config)
        assert score == 0.0

    async def test_all_pass_zero_score(self) -> None:
        """all_pass fails when a run outputs 0 (score not > 0)."""
        config = InnerLoopConfig(runs_per_cycle=2, aggregate=AggregateMethod.all_pass)
        score = await execute_multi_run("echo 0", config)
        assert score == 0.0

    async def test_cwd_parameter(self, tmp_path: Path) -> None:
        """cwd parameter is passed to subprocess."""
        config = InnerLoopConfig(runs_per_cycle=1, aggregate=AggregateMethod.mean)
        score = await execute_multi_run("echo 0.75", config, cwd=tmp_path)
        assert score == pytest.approx(0.75)


# ── detect_plateau ──────────────────────────────────────────────


class TestDetectPlateau:
    def test_empty_history(self) -> None:
        assert detect_plateau([], threshold=3) is False

    def test_below_threshold(self) -> None:
        history = [_make_record(1, 0.5), _make_record(2, 0.5)]
        assert detect_plateau(history, threshold=3) is False

    def test_at_threshold_no_plateau(self) -> None:
        """Three experiments with continuous improvement = no plateau."""
        history = [
            _make_record(1, 0.5),
            _make_record(2, 0.6),
            _make_record(3, 0.7),
        ]
        assert detect_plateau(history, threshold=3) is False

    def test_at_threshold_with_plateau(self) -> None:
        """Three experiments where last 3 show no improvement."""
        history = [
            _make_record(1, 0.8),
            _make_record(2, 0.7),
            _make_record(3, 0.75),
            _make_record(4, 0.78),
        ]
        # After record 1 (best=0.8), records 2, 3, 4 never exceed 0.8
        # streak = 3 >= threshold=3
        assert detect_plateau(history, threshold=3) is True

    def test_above_threshold(self) -> None:
        """Five flat experiments with threshold=3 => plateau."""
        history = [
            _make_record(1, 0.5),
            _make_record(2, 0.5),
            _make_record(3, 0.5),
            _make_record(4, 0.5),
            _make_record(5, 0.5),
        ]
        assert detect_plateau(history, threshold=3) is True

    def test_improvement_resets_streak(self) -> None:
        """A late improvement resets the no-improvement streak."""
        history = [
            _make_record(1, 0.5),
            _make_record(2, 0.5),  # no improvement
            _make_record(3, 0.5),  # no improvement
            _make_record(4, 0.6),  # improvement! resets streak
            _make_record(5, 0.55),  # no improvement (streak=1)
        ]
        assert detect_plateau(history, threshold=3) is False

    def test_none_scores_skipped(self) -> None:
        """Records with score_after=None are excluded from plateau analysis."""
        history = [
            _make_record(1, 0.5),
            _make_record(2, None),
            _make_record(3, 0.5),
            _make_record(4, None),
            _make_record(5, 0.5),
        ]
        # Only 3 scored records: [0.5, 0.5, 0.5]
        # After first (best=0.5), two non-improvements -> streak=2 < threshold=3
        assert detect_plateau(history, threshold=3) is False

    def test_custom_threshold(self) -> None:
        """Plateau detected with custom threshold=2."""
        history = [
            _make_record(1, 0.5),
            _make_record(2, 0.5),
            _make_record(3, 0.5),
        ]
        assert detect_plateau(history, threshold=2) is True

    def test_single_record(self) -> None:
        """Single record is never a plateau."""
        assert detect_plateau([_make_record(1, 0.5)], threshold=1) is False


# ── checkpoint extensions ───────────────────────────────────────


class TestCheckpointExtensions:
    def test_new_fields_defaults(self) -> None:
        state = CheckpointState(
            mode="research",
            active_experiment_id=None,
            completed_agents=[],
            pending_agents=[],
            last_eval_scores={},
            current_hypothesis=None,
            timestamp="2026-05-24T00:00:00",
        )
        assert state.plateau_count == 0
        assert state.loop_level == "inner"

    def test_new_fields_custom(self) -> None:
        state = CheckpointState(
            mode="research",
            active_experiment_id=5,
            completed_agents=["researcher"],
            pending_agents=["builder"],
            last_eval_scores={"accuracy": 0.85},
            current_hypothesis="Try larger model",
            plateau_count=3,
            loop_level="outer",
            timestamp="2026-05-24T12:00:00",
        )
        assert state.plateau_count == 3
        assert state.loop_level == "outer"

    def test_save_load_roundtrip_with_new_fields(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / ".factory").mkdir()

        state = CheckpointState(
            mode="research",
            active_experiment_id=10,
            completed_agents=["researcher", "strategist"],
            pending_agents=["builder"],
            last_eval_scores={"acc": 0.9},
            current_hypothesis="Scale up",
            completed_hypotheses=[1, 2, 3],
            plateau_count=2,
            loop_level="outer",
            timestamp="2026-05-24T15:00:00",
        )
        save_checkpoint(project, state)
        loaded = load_checkpoint(project)
        assert loaded is not None
        assert loaded.plateau_count == 2
        assert loaded.loop_level == "outer"
        assert loaded == state

    def test_backward_compat_without_new_fields(self, tmp_path: Path) -> None:
        """Old checkpoint JSON without plateau_count/loop_level should use defaults."""
        project = tmp_path / "project"
        project.mkdir()
        (project / ".factory").mkdir()

        old_data = {
            "mode": "improve",
            "active_experiment_id": None,
            "completed_agents": [],
            "pending_agents": [],
            "last_eval_scores": {},
            "current_hypothesis": None,
            "timestamp": "2026-01-01T00:00:00",
        }
        (project / ".factory" / "checkpoint.json").write_text(json.dumps(old_data))

        loaded = load_checkpoint(project)
        assert loaded is not None
        assert loaded.plateau_count == 0
        assert loaded.loop_level == "inner"

    def test_format_checkpoint_includes_loop_info(self) -> None:
        state = CheckpointState(
            mode="research",
            active_experiment_id=5,
            completed_agents=[],
            pending_agents=[],
            last_eval_scores={},
            current_hypothesis=None,
            plateau_count=2,
            loop_level="outer",
            timestamp="2026-05-24T00:00:00",
        )
        output = format_checkpoint(state)
        assert "research" in output
