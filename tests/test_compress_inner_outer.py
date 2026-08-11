"""Tests for factory.compress package: CompressEvaluator, CompressInnerLoop, CompressOuterLoop."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.compress.evaluator import CompressEvaluator
from factory.compress.inner_loop import CompressInnerLoop, _DEFAULT_FROZEN_NODES
from factory.compress.outer_loop import CompressOuterLoop, OuterLoopResult
from factory.cycle_analyzer import CycleRecord, ExperimentRecord


# ── CompressEvaluator ─────────────────────────────────────────────


class TestCompressEvaluatorParse:
    def test_parse_valid_artifact(self, tmp_path: Path) -> None:
        artifact = tmp_path / "result.json"
        artifact.write_text(json.dumps({
            "compression_ratio": 4.0,
            "quality_retention": 0.95,
            "inference_latency": 50.0,
            "technique": "pruning",
        }))
        evaluator = CompressEvaluator()
        result = evaluator.parse(artifact)

        assert result.valid is True
        assert result.score > 0.0
        assert result.metrics["compression_ratio"] == 4.0
        assert result.metrics["quality_retention"] == 0.95
        assert str(artifact) in result.artifacts

    def test_parse_missing_file(self, tmp_path: Path) -> None:
        evaluator = CompressEvaluator()
        result = evaluator.parse(tmp_path / "nonexistent.json")

        assert result.valid is False
        assert result.score == 0.0

    def test_parse_malformed_json(self, tmp_path: Path) -> None:
        artifact = tmp_path / "bad.json"
        artifact.write_text("not valid json {{{")
        evaluator = CompressEvaluator()
        result = evaluator.parse(artifact)

        assert result.valid is False
        assert result.score == 0.0

    def test_parse_missing_fields(self, tmp_path: Path) -> None:
        artifact = tmp_path / "incomplete.json"
        artifact.write_text(json.dumps({"technique": "pruning"}))
        evaluator = CompressEvaluator()
        result = evaluator.parse(artifact)

        assert result.valid is False
        assert result.score == 0.0

    def test_parse_many_best_score(self, tmp_path: Path) -> None:
        low = tmp_path / "low.json"
        low.write_text(json.dumps({
            "compression_ratio": 2.0,
            "quality_retention": 0.8,
            "inference_latency": 100.0,
        }))
        high = tmp_path / "high.json"
        high.write_text(json.dumps({
            "compression_ratio": 8.0,
            "quality_retention": 0.98,
            "inference_latency": 10.0,
        }))
        evaluator = CompressEvaluator()
        result = evaluator.parse_many([low, high])

        assert result.valid is True
        high_score = evaluator.parse(high).score
        assert result.score == pytest.approx(high_score)

    def test_get_info(self) -> None:
        evaluator = CompressEvaluator()
        info = evaluator.get_info()

        assert info["benchmark"] == "compression"
        assert "compression_ratio" in info["weights"]
        assert "quality_retention" in info["weights"]
        assert "latency" in info["weights"]
        assert "compression_ratio" in info["metrics"]

    def test_combined_score_formula(self) -> None:
        evaluator = CompressEvaluator(
            compression_weight=0.4,
            quality_weight=0.5,
            latency_weight=0.1,
        )
        # compression_ratio=4.0, quality_retention=0.95, latency=0.0
        # latency_penalty = 1/(1+0/1000) = 1.0
        # score = 4.0*0.4 + 0.95*0.5 - (1.0 - 1.0)*0.1 = 1.6 + 0.475 - 0.0 = 2.075
        score = evaluator._compute_combined_score(4.0, 0.95, 0.0)
        assert score == pytest.approx(2.075)

        # with latency=500ms: penalty = 1/(1+500/1000) = 1/1.5 = 0.6667
        # score = 1.6 + 0.475 - (1.0 - 0.6667)*0.1 = 2.075 - 0.03333 = 2.04167
        score_latency = evaluator._compute_combined_score(4.0, 0.95, 500.0)
        assert score_latency < score
        expected = 4.0 * 0.4 + 0.95 * 0.5 - (1.0 - 1.0 / 1.5) * 0.1
        assert score_latency == pytest.approx(expected)

    def test_custom_weights(self, tmp_path: Path) -> None:
        artifact = tmp_path / "result.json"
        artifact.write_text(json.dumps({
            "compression_ratio": 4.0,
            "quality_retention": 0.95,
            "inference_latency": 0.0,
        }))
        default_eval = CompressEvaluator()
        custom_eval = CompressEvaluator(compression_weight=0.8, quality_weight=0.2, latency_weight=0.0)

        default_score = default_eval.parse(artifact).score
        custom_score = custom_eval.parse(artifact).score
        assert default_score != custom_score


# ── CompressInnerLoop ─────────────────────────────────────────────


class TestCompressInnerLoopInit:
    def test_defaults(self, tmp_path: Path) -> None:
        loop = CompressInnerLoop(project_dir=tmp_path)

        assert loop.mode == "compress"
        assert loop.frozen_nodes == _DEFAULT_FROZEN_NODES
        assert isinstance(loop.evaluator, CompressEvaluator)

    def test_custom_evaluator(self, tmp_path: Path) -> None:
        custom = CompressEvaluator(compression_weight=0.8)
        loop = CompressInnerLoop(project_dir=tmp_path, evaluator=custom)

        assert loop.evaluator is custom

    def test_frozen_nodes_override(self, tmp_path: Path) -> None:
        custom_frozen = frozenset({"study", "finalize"})
        loop = CompressInnerLoop(project_dir=tmp_path, frozen_nodes=custom_frozen)

        assert loop.frozen_nodes == custom_frozen

    def test_frozen_nodes_validation_with_workflow(self, tmp_path: Path) -> None:
        from factory.workflow.primitives import Workflow, FnNode

        wf = Workflow(
            name="test",
            nodes={
                "study": FnNode(id="study", command="echo study"),
                "finalize": FnNode(id="finalize", command="echo finalize"),
            },
            edges=[],
            start_node="study",
        )
        loop = CompressInnerLoop(
            project_dir=tmp_path,
            workflow=wf,
            frozen_nodes=frozenset({"study"}),
        )
        assert loop.is_mutable("finalize")
        assert not loop.is_mutable("study")


class TestCompressInnerLoopMethods:
    def _make_loop_with_history(self, tmp_path: Path) -> CompressInnerLoop:
        """Create a loop with fake history records containing eval artifacts."""
        loop = CompressInnerLoop(project_dir=tmp_path)

        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        for i, (ratio, quality, technique) in enumerate([
            (2.0, 0.9, "pruning"),
            (4.0, 0.85, "quantization"),
            (6.0, 0.92, "distillation"),
        ]):
            artifact = artifact_dir / f"eval_{i}.json"
            artifact.write_text(json.dumps({
                "compression_ratio": ratio,
                "quality_retention": quality,
                "inference_latency": 50.0,
                "technique": technique,
            }))
            record = CycleRecord(
                cycle_number=i + 1,
                mode="compress",
                started_at=None,
                ended_at=None,
                duration_s=10.0,
                score_start=None,
                score_end=loop.evaluator.parse(artifact).score,
                score_delta=None,
                experiments=[ExperimentRecord(
                    exp_id=i + 1,
                    hypothesis=f"try {technique}",
                    verdict="keep",
                    score_before=0.0,
                    score_after=loop.evaluator.parse(artifact).score,
                    score_delta=0.0,
                    cost_usd=0.5,
                    duration_s=10.0,
                    eval_artifacts=[str(artifact)],
                )],
            )
            loop._history.append(record)

        return loop

    def test_technique_history(self, tmp_path: Path) -> None:
        loop = self._make_loop_with_history(tmp_path)
        history = loop.technique_history()

        assert len(history) == 3
        assert history[0]["technique"] == "pruning"
        assert history[1]["technique"] == "quantization"
        assert history[2]["technique"] == "distillation"
        assert all(h["score"] is not None for h in history)

    def test_best_technique(self, tmp_path: Path) -> None:
        loop = self._make_loop_with_history(tmp_path)
        best = loop.best_technique()

        assert best is not None
        assert best["technique"] == "distillation"
        assert best["score"] is not None

    def test_best_technique_empty(self, tmp_path: Path) -> None:
        loop = CompressInnerLoop(project_dir=tmp_path)
        assert loop.best_technique() is None

    def test_compression_trajectory(self, tmp_path: Path) -> None:
        loop = self._make_loop_with_history(tmp_path)
        trajectory = loop.compression_trajectory()

        assert len(trajectory) == 3
        assert trajectory[0]["ratio"] == 2.0
        assert trajectory[0]["quality"] == 0.9
        assert trajectory[0]["technique"] == "pruning"
        assert trajectory[2]["ratio"] == 6.0


# ── CompressOuterLoop ─────────────────────────────────────────────


class TestCompressOuterLoopDirectives:
    def test_no_plateau(self, tmp_path: Path) -> None:
        inner = CompressInnerLoop(project_dir=tmp_path)
        outer = CompressOuterLoop(inner=inner, budget=20)

        directives = outer._analyze_and_steer(plateau_detected=False)
        assert directives == {}

    def test_first_plateau(self, tmp_path: Path) -> None:
        inner = CompressInnerLoop(project_dir=tmp_path)
        outer = CompressOuterLoop(inner=inner, budget=20)

        directives = outer._analyze_and_steer(plateau_detected=True)
        assert directives["escalation"] == "inner"
        assert outer._plateau_count == 1

    def test_second_plateau(self, tmp_path: Path) -> None:
        inner = CompressInnerLoop(project_dir=tmp_path)
        outer = CompressOuterLoop(inner=inner, budget=20)

        outer._analyze_and_steer(plateau_detected=True)
        directives = outer._analyze_and_steer(plateau_detected=True)
        assert directives["escalation"] == "outer"
        assert outer._plateau_count == 2

    def test_third_plateau_converge(self, tmp_path: Path) -> None:
        inner = CompressInnerLoop(project_dir=tmp_path)
        outer = CompressOuterLoop(inner=inner, budget=20)

        outer._analyze_and_steer(plateau_detected=True)
        outer._analyze_and_steer(plateau_detected=True)
        directives = outer._analyze_and_steer(plateau_detected=True)
        assert directives["escalation"] == "converge"
        assert outer._plateau_count == 3


class TestCompressOuterLoopConvergence:
    def test_converged_by_plateau(self, tmp_path: Path) -> None:
        inner = CompressInnerLoop(project_dir=tmp_path)
        outer = CompressOuterLoop(inner=inner, budget=20)
        outer._plateau_count = 3

        assert outer._converged() is True

    def test_converged_by_budget(self, tmp_path: Path) -> None:
        inner = CompressInnerLoop(project_dir=tmp_path)
        outer = CompressOuterLoop(inner=inner, budget=5)
        outer._cycle = 5

        assert outer._converged() is True

    def test_not_converged(self, tmp_path: Path) -> None:
        inner = CompressInnerLoop(project_dir=tmp_path)
        outer = CompressOuterLoop(inner=inner, budget=20)

        assert outer._converged() is False

    def test_run_respects_max_cycles(self, tmp_path: Path) -> None:
        (tmp_path / ".factory").mkdir(parents=True, exist_ok=True)
        inner = CompressInnerLoop(project_dir=tmp_path)
        outer = CompressOuterLoop(inner=inner, budget=3)

        with patch.object(inner, "step", return_value=CycleRecord(
            cycle_number=1, mode="compress", started_at=None,
            ended_at=None, duration_s=1.0, score_start=None,
            score_end=0.5, score_delta=None,
        )):
            result = outer.run()

        assert isinstance(result, OuterLoopResult)
        assert result.cycles_completed == 3
        assert result.convergence_reason == "max_cycles"

    def test_summarize_converged(self, tmp_path: Path) -> None:
        inner = CompressInnerLoop(project_dir=tmp_path)
        outer = CompressOuterLoop(inner=inner, budget=20)
        outer._plateau_count = 3
        outer._cycle = 5

        result = outer._summarize()
        assert result.convergence_reason == "converged"
        assert result.cycles_completed == 5
        assert result.plateau_count == 3
