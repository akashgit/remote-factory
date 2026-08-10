"""Tests for factory.optimization.benchmarks.featurebench — FeatureBench benchmark adapter."""

from __future__ import annotations

import json
from pathlib import Path

from factory.optimization.benchmarks.featurebench import (
    FeatureBenchEvaluator,
    build_featurebench_config,
    build_featurebench_executor,
    build_featurebench_surface,
)
from factory.optimization.benchmarks.harbor import HarborBenchmark
from factory.optimization.protocols import Evaluator
from factory.optimization.surface import Surface
from factory.optimization.types import LoopConfig


class TestFeatureBenchEvaluator:
    def test_parse_valid_resolved_rate(self, tmp_path: Path) -> None:
        artifact = tmp_path / "results.json"
        artifact.write_text(json.dumps({"resolved_rate": 0.75, "test_pass_rate": 0.9}))
        result = FeatureBenchEvaluator().parse(artifact)
        assert result.score == 0.75
        assert result.valid is True
        assert result.metrics == {"resolved_rate": 0.75, "test_pass_rate": 0.9}
        assert str(artifact) in result.artifacts

    def test_parse_fallback_score_key(self, tmp_path: Path) -> None:
        artifact = tmp_path / "results.json"
        artifact.write_text(json.dumps({"score": 0.6}))
        result = FeatureBenchEvaluator().parse(artifact)
        assert result.score == 0.6
        assert result.valid is True

    def test_parse_malformed_json(self, tmp_path: Path) -> None:
        artifact = tmp_path / "results.json"
        artifact.write_text("not json {{{")
        result = FeatureBenchEvaluator().parse(artifact)
        assert result.score == 0.0
        assert result.valid is False

    def test_parse_missing_file(self, tmp_path: Path) -> None:
        result = FeatureBenchEvaluator().parse(tmp_path / "nonexistent.json")
        assert result.score == 0.0
        assert result.valid is False

    def test_parse_many_returns_best(self, tmp_path: Path) -> None:
        for i, score in enumerate([0.4, 0.85, 0.6]):
            p = tmp_path / f"results_{i}.json"
            p.write_text(json.dumps({"resolved_rate": score}))
        paths = [tmp_path / f"results_{i}.json" for i in range(3)]
        result = FeatureBenchEvaluator().parse_many(paths)
        assert result.score == 0.85
        assert result.valid is True

    def test_get_info(self) -> None:
        info = FeatureBenchEvaluator(target=0.90).get_info()
        assert info["benchmark"] == "featurebench"
        assert info["target"] == 0.90
        assert isinstance(info["metrics"], list)
        assert "resolved_rate" in info["metrics"]
        assert "test_pass_rate" in info["metrics"]

    def test_protocol_conformance(self) -> None:
        assert isinstance(FeatureBenchEvaluator(), Evaluator)


class TestBuildFeaturebenchSurface:
    def test_build_surface(self) -> None:
        surface = build_featurebench_surface()
        assert isinstance(surface, Surface)
        assert surface.workflow is not None
        assert surface.frozen_nodes == frozenset({"study", "auto_merge"})
        assert surface.prompt_slots == {}
        mutable = surface.mutable_nodes()
        assert "builder" in mutable
        assert "gate_verify" in mutable
        assert "study" not in mutable
        assert "auto_merge" not in mutable


class TestBuildFeaturebenchConfig:
    def test_build_config(self) -> None:
        config = build_featurebench_config()
        assert isinstance(config, LoopConfig)
        assert config.epochs == 3
        assert config.steps_per_epoch == 5

    def test_custom_values(self) -> None:
        config = build_featurebench_config(epochs=10, steps_per_epoch=20)
        assert config.epochs == 10
        assert config.steps_per_epoch == 20


class TestBuildFeaturebenchExecutor:
    def test_build_executor(self) -> None:
        executor = build_featurebench_executor()
        assert isinstance(executor, HarborBenchmark)
        assert executor.agent_class == "factory_harbor_agent:FeaturebenchFactoryCeo"
        assert executor.dataset == "featurebench"

    def test_build_executor_default_model(self) -> None:
        executor = build_featurebench_executor()
        assert executor.model == "opus"
