"""Integration tests for inner/outer loop infrastructure.

Covers:
- factory.md -> config.json round-trip with Multi-Run and Surface Scoping
- execute_multi_run() with deterministic commands and all aggregation methods
- detect_plateau() with various history shapes
- CheckpointState with new plateau_count and loop_level fields
- Model validation for InnerLoopConfig, OuterLoopConfig, FactoryConfig
- Parser tests for _parse_inner_loop, _parse_outer_loop
"""

from __future__ import annotations

import json
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
    FactoryConfig,
    InnerLoopConfig,
    OuterLoopConfig,
    ResearchTarget,
)
from factory.research.runner import aggregate_metric, execute_multi_run
from factory.store import ExperimentStore, _parse_inner_loop, _parse_outer_loop
from factory.strategy import detect_plateau, detect_research_plateau


# ── Model validation ────────────────────────────────────────────


class TestInnerLoopConfig:
    def test_defaults(self) -> None:
        config = InnerLoopConfig()
        assert config.runs_per_cycle == 1
        assert config.aggregate == AggregateMethod.mean
        assert config.plateau_threshold == 3
        assert config.max_inner_runs_per_cycle is None

    def test_custom_values(self) -> None:
        config = InnerLoopConfig(
            runs_per_cycle=5,
            aggregate=AggregateMethod.median,
            plateau_threshold=5,
            max_inner_runs_per_cycle=10,
        )
        assert config.runs_per_cycle == 5
        assert config.aggregate == AggregateMethod.median
        assert config.plateau_threshold == 5
        assert config.max_inner_runs_per_cycle == 10

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(Exception):
            InnerLoopConfig(runs_per_cycle=1, unknown_field="x")  # type: ignore[call-arg]

    def test_all_aggregate_methods(self) -> None:
        for method in AggregateMethod:
            config = InnerLoopConfig(aggregate=method)
            assert config.aggregate == method


class TestOuterLoopConfig:
    def test_defaults(self) -> None:
        config = OuterLoopConfig()
        assert config.max_outer_cycles is None
        assert config.inner_surfaces == []
        assert config.outer_surfaces == []

    def test_custom_values(self) -> None:
        config = OuterLoopConfig(
            max_outer_cycles=5,
            inner_surfaces=["prompts/*.md"],
            outer_surfaces=["src/**/*.py"],
        )
        assert config.max_outer_cycles == 5
        assert config.inner_surfaces == ["prompts/*.md"]
        assert config.outer_surfaces == ["src/**/*.py"]

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(Exception):
            OuterLoopConfig(bad="field")  # type: ignore[call-arg]


class TestFactoryConfigWithLoops:
    def test_inner_loop_none_by_default(self) -> None:
        config = FactoryConfig(
            goal="test",
            scope=[],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
        )
        assert config.inner_loop is None
        assert config.outer_loop is None

    def test_with_inner_loop(self) -> None:
        config = FactoryConfig(
            goal="test",
            scope=[],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
            inner_loop=InnerLoopConfig(runs_per_cycle=3),
        )
        assert config.inner_loop is not None
        assert config.inner_loop.runs_per_cycle == 3

    def test_with_outer_loop(self) -> None:
        config = FactoryConfig(
            goal="test",
            scope=[],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
            outer_loop=OuterLoopConfig(inner_surfaces=["a.md"], outer_surfaces=["b.py"]),
        )
        assert config.outer_loop is not None
        assert config.outer_loop.inner_surfaces == ["a.md"]


# ── Parser tests ────────────────────────────────────────────────


class TestParseInnerLoop:
    def test_empty_returns_none(self) -> None:
        assert _parse_inner_loop([]) is None
        assert _parse_inner_loop("") is None
        assert _parse_inner_loop(0.0) is None

    def test_basic_parsing(self) -> None:
        items = ["runs_per_cycle: 5", "aggregate: median", "plateau_threshold: 4"]
        result = _parse_inner_loop(items)
        assert result is not None
        assert result.runs_per_cycle == 5
        assert result.aggregate == AggregateMethod.median
        assert result.plateau_threshold == 4
        assert result.max_inner_runs_per_cycle is None

    def test_with_max_inner_runs(self) -> None:
        items = ["runs_per_cycle: 3", "aggregate: max", "max_inner_runs_per_cycle: 10"]
        result = _parse_inner_loop(items)
        assert result is not None
        assert result.runs_per_cycle == 3
        assert result.aggregate == AggregateMethod.max
        assert result.max_inner_runs_per_cycle == 10

    def test_defaults_when_partial(self) -> None:
        items = ["runs_per_cycle: 2"]
        result = _parse_inner_loop(items)
        assert result is not None
        assert result.runs_per_cycle == 2
        assert result.aggregate == AggregateMethod.mean
        assert result.plateau_threshold == 3


class TestParseOuterLoop:
    def test_empty_returns_none(self) -> None:
        assert _parse_outer_loop([]) is None
        assert _parse_outer_loop("") is None
        assert _parse_outer_loop(0.0) is None

    def test_basic_parsing(self) -> None:
        items = [
            "max_outer_cycles: 5",
            "inner: prompts/*.md",
            "inner: config/*.yaml",
            "outer: src/**/*.py",
        ]
        result = _parse_outer_loop(items)
        assert result is not None
        assert result.max_outer_cycles == 5
        assert result.inner_surfaces == ["prompts/*.md", "config/*.yaml"]
        assert result.outer_surfaces == ["src/**/*.py"]

    def test_surfaces_only(self) -> None:
        items = ["inner: a.md", "outer: b.py"]
        result = _parse_outer_loop(items)
        assert result is not None
        assert result.max_outer_cycles is None
        assert result.inner_surfaces == ["a.md"]
        assert result.outer_surfaces == ["b.py"]

    def test_no_relevant_items_returns_none(self) -> None:
        items = ["something: else", "random text"]
        result = _parse_outer_loop(items)
        assert result is None


# ── Aggregation tests ───────────────────────────────────────────


class TestAggregateMetric:
    def test_mean(self) -> None:
        assert aggregate_metric([0.2, 0.4, 0.6], AggregateMethod.mean) == pytest.approx(0.4)

    def test_median_odd(self) -> None:
        assert aggregate_metric([0.1, 0.5, 0.9], AggregateMethod.median) == pytest.approx(0.5)

    def test_median_even(self) -> None:
        assert aggregate_metric([0.2, 0.4, 0.6, 0.8], AggregateMethod.median) == pytest.approx(0.5)

    def test_max(self) -> None:
        assert aggregate_metric([0.1, 0.3, 0.9], AggregateMethod.max) == pytest.approx(0.9)

    def test_all_pass(self) -> None:
        assert aggregate_metric([0.5, 0.3, 0.7], AggregateMethod.all_pass) == pytest.approx(0.3)

    def test_empty_returns_zero(self) -> None:
        assert aggregate_metric([], AggregateMethod.mean) == 0.0

    def test_single_value(self) -> None:
        for method in AggregateMethod:
            assert aggregate_metric([0.42], method) == pytest.approx(0.42)


# ── Multi-run execution tests ──────────────────────────────────


class TestExecuteMultiRun:
    async def test_multi_run_aggregates(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory" / "research" / "runs").mkdir(parents=True)

        result_file = project / "result.json"
        result_file.write_text(json.dumps({"score": 0.5}))

        script = project / "run.sh"
        script.write_text("#!/bin/bash\necho '{\"score\": 0.5}' > result.json\n")
        script.chmod(0o755)

        config = ResearchTarget(
            objective="test",
            metric="score",
            target=1.0,
            run_command=f"bash {script}",
            result_path="result.json",
            timeout=30,
        )
        inner = InnerLoopConfig(runs_per_cycle=3, aggregate=AggregateMethod.mean)

        summary = await execute_multi_run(project, config, "cycle-001", inner)

        assert summary["aggregate"] == "mean"
        assert len(summary["runs"]) == 3
        assert "metric_value" in summary
        assert summary["duration_seconds"] > 0

    async def test_multi_run_respects_max_cap(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory" / "research" / "runs").mkdir(parents=True)

        result_file = project / "result.json"
        result_file.write_text(json.dumps({"score": 0.5}))

        script = project / "run.sh"
        script.write_text("#!/bin/bash\necho '{\"score\": 0.5}' > result.json\n")
        script.chmod(0o755)

        config = ResearchTarget(
            objective="test",
            metric="score",
            target=1.0,
            run_command=f"bash {script}",
            result_path="result.json",
            timeout=30,
        )
        inner = InnerLoopConfig(
            runs_per_cycle=10,
            aggregate=AggregateMethod.max,
            max_inner_runs_per_cycle=2,
        )

        summary = await execute_multi_run(project, config, "cycle-002", inner)
        assert len(summary["runs"]) == 2


# ── Plateau detection tests ────────────────────────────────────


class TestDetectResearchPlateau:
    def test_not_enough_data(self) -> None:
        summaries = [{"metric_value": 0.5}, {"metric_value": 0.5}]
        assert detect_research_plateau(summaries, threshold=3) is False

    def test_plateau_detected(self) -> None:
        summaries = [
            {"metric_value": 0.5},
            {"metric_value": 0.5},
            {"metric_value": 0.5},
            {"metric_value": 0.5},
        ]
        assert detect_research_plateau(summaries, threshold=3) is True

    def test_no_plateau_with_improvement(self) -> None:
        summaries = [
            {"metric_value": 0.3},
            {"metric_value": 0.4},
            {"metric_value": 0.5},
            {"metric_value": 0.6},
        ]
        assert detect_research_plateau(summaries, threshold=3) is False

    def test_plateau_with_stagnation_after_improvement(self) -> None:
        summaries = [
            {"metric_value": 0.3},
            {"metric_value": 0.5},
            {"metric_value": 0.5},
            {"metric_value": 0.5},
            {"metric_value": 0.5},
        ]
        assert detect_research_plateau(summaries, threshold=3) is True

    def test_custom_threshold(self) -> None:
        summaries = [
            {"metric_value": 0.5},
            {"metric_value": 0.5},
            {"metric_value": 0.5},
        ]
        assert detect_research_plateau(summaries, threshold=2) is True

    def test_improvement_in_window_breaks_plateau(self) -> None:
        summaries = [
            {"metric_value": 0.3},
            {"metric_value": 0.3},
            {"metric_value": 0.3},
            {"metric_value": 0.4},
        ]
        assert detect_research_plateau(summaries, threshold=3) is False


# ── Checkpoint extension tests ──────────────────────────────────


class TestCheckpointStateExtension:
    def test_default_values(self) -> None:
        state = CheckpointState(
            mode="research",
            active_experiment_id=None,
            completed_agents=[],
            pending_agents=[],
            last_eval_scores={},
            current_hypothesis=None,
            timestamp="2026-01-01T00:00:00Z",
        )
        assert state.plateau_count == 0
        assert state.loop_level == "inner"

    def test_custom_values(self) -> None:
        state = CheckpointState(
            mode="research",
            active_experiment_id=1,
            completed_agents=["baseline"],
            pending_agents=["builder"],
            last_eval_scores={"score": 0.5},
            current_hypothesis="test",
            timestamp="2026-01-01T00:00:00Z",
            plateau_count=2,
            loop_level="outer",
        )
        assert state.plateau_count == 2
        assert state.loop_level == "outer"

    def test_serialization_roundtrip(self) -> None:
        state = CheckpointState(
            mode="research",
            active_experiment_id=None,
            completed_agents=[],
            pending_agents=[],
            last_eval_scores={},
            current_hypothesis=None,
            timestamp="2026-01-01T00:00:00Z",
            plateau_count=3,
            loop_level="outer",
        )
        data = json.loads(state.model_dump_json())
        restored = CheckpointState.model_validate(data)
        assert restored.plateau_count == 3
        assert restored.loop_level == "outer"


class TestCheckpointSaveLoadFormat:
    """Tests for save/load roundtrip, backward compat, and format_checkpoint."""

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
        assert "Loop level:    outer" in output
        assert "Plateau count: 2" in output

    def test_format_checkpoint_omits_zero_plateau(self) -> None:
        state = CheckpointState(
            mode="research",
            active_experiment_id=None,
            completed_agents=[],
            pending_agents=[],
            last_eval_scores={},
            current_hypothesis=None,
            plateau_count=0,
            loop_level="inner",
            timestamp="2026-05-24T00:00:00",
        )
        output = format_checkpoint(state)
        assert "Loop level:    inner" in output
        assert "Plateau count" not in output

    def test_loop_level_rejects_invalid(self) -> None:
        """loop_level only accepts 'inner' or 'outer'."""
        with pytest.raises(Exception):
            CheckpointState(
                mode="research",
                active_experiment_id=None,
                completed_agents=[],
                pending_agents=[],
                last_eval_scores={},
                current_hypothesis=None,
                loop_level="invalid",
                timestamp="2026-05-24T00:00:00",
            )


# ── Integration: factory.md -> config.json -> FactoryConfig ──────


class TestFactoryMdRoundTrip:
    async def test_inner_loop_roundtrip(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        factory_md = project / "factory.md"
        factory_md.write_text(
            "## Goal\nTest project\n\n"
            "## Scope\n### Modifiable\n- src/*.py\n\n"
            "## Guards\n- no secrets\n\n"
            "## Eval\n### Command\n```bash\necho ok\n```\n\n"
            "### Threshold\n0.8\n\n"
            "## Constraints\n- be careful\n\n"
            "## Inner Loop\n"
            "- runs_per_cycle: 5\n"
            "- aggregate: median\n"
            "- plateau_threshold: 4\n\n"
            "## Outer Loop Surfaces\n"
            "- max_outer_cycles: 3\n"
            "- inner: prompts/*.md\n"
            "- outer: src/**/*.py\n"
            "- outer: agents/**/*.md\n"
        )

        store = ExperimentStore(project)
        config = await store.reparse_config()

        assert config.inner_loop is not None
        assert config.inner_loop.runs_per_cycle == 5
        assert config.inner_loop.aggregate == AggregateMethod.median
        assert config.inner_loop.plateau_threshold == 4

        assert config.outer_loop is not None
        assert config.outer_loop.max_outer_cycles == 3
        assert config.outer_loop.inner_surfaces == ["prompts/*.md"]
        assert config.outer_loop.outer_surfaces == ["src/**/*.py", "agents/**/*.md"]

        config_json = json.loads((project / ".factory" / "config.json").read_text())
        assert config_json["inner_loop"]["runs_per_cycle"] == 5
        assert config_json["outer_loop"]["outer_surfaces"] == ["src/**/*.py", "agents/**/*.md"]

        restored = FactoryConfig(**config_json)
        assert restored.inner_loop is not None
        assert restored.inner_loop.aggregate == AggregateMethod.median
        assert restored.outer_loop is not None
        assert restored.outer_loop.max_outer_cycles == 3

    async def test_no_loop_config_roundtrip(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()

        factory_md = project / "factory.md"
        factory_md.write_text(
            "## Goal\nTest project\n\n"
            "## Scope\n### Modifiable\n- src/*.py\n\n"
            "## Guards\n- no secrets\n\n"
            "## Eval\n### Command\n```bash\necho ok\n```\n\n"
            "### Threshold\n0.8\n\n"
            "## Constraints\n- be careful\n"
        )

        store = ExperimentStore(project)
        config = await store.reparse_config()

        assert config.inner_loop is None
        assert config.outer_loop is None
