"""Tests for factory.optimization.benchmarks.searchqa — SearchQA benchmark adapter."""

from __future__ import annotations

import json
from pathlib import Path

from factory.inner_loop import EvalResult
from factory.optimization.benchmarks.searchqa import (
    SearchQAEvaluator,
    build_searchqa_config,
    build_searchqa_executor,
    build_searchqa_surface,
)
from factory.optimization.executors.harbor import HarborExecutor
from factory.optimization.protocols import Evaluator
from factory.optimization.surface import Surface
from factory.optimization.types import LoopConfig


class TestSearchQAEvaluator:
    def test_parse_valid_json_with_accuracy(self, tmp_path: Path) -> None:
        artifact = tmp_path / "reward.json"
        artifact.write_text(json.dumps({"accuracy": 0.82, "em": 0.75, "f1": 0.88}))
        result = SearchQAEvaluator().parse(artifact)
        assert result.score == 0.82
        assert result.valid is True
        assert result.metrics == {"accuracy": 0.82, "em": 0.75, "f1": 0.88}
        assert str(artifact) in result.artifacts

    def test_parse_valid_json_with_score_fallback(self, tmp_path: Path) -> None:
        artifact = tmp_path / "reward.json"
        artifact.write_text(json.dumps({"score": 0.65}))
        result = SearchQAEvaluator().parse(artifact)
        assert result.score == 0.65
        assert result.valid is True

    def test_parse_invalid_json(self, tmp_path: Path) -> None:
        artifact = tmp_path / "reward.json"
        artifact.write_text("not json {{{")
        result = SearchQAEvaluator().parse(artifact)
        assert result.score == 0.0
        assert result.valid is False

    def test_parse_missing_file(self, tmp_path: Path) -> None:
        result = SearchQAEvaluator().parse(tmp_path / "nonexistent.json")
        assert result.score == 0.0
        assert result.valid is False

    def test_parse_many_selects_best(self, tmp_path: Path) -> None:
        for i, score in enumerate([0.3, 0.9, 0.6]):
            p = tmp_path / f"reward_{i}.json"
            p.write_text(json.dumps({"accuracy": score}))
        paths = [tmp_path / f"reward_{i}.json" for i in range(3)]
        result = SearchQAEvaluator().parse_many(paths)
        assert result.score == 0.9
        assert result.valid is True

    def test_parse_many_empty_list(self) -> None:
        result = SearchQAEvaluator().parse_many([])
        assert result.score == 0.0
        assert result.valid is False

    def test_parse_many_single_item(self, tmp_path: Path) -> None:
        artifact = tmp_path / "reward.json"
        artifact.write_text(json.dumps({"accuracy": 0.55}))
        result = SearchQAEvaluator().parse_many([artifact])
        assert result.score == 0.55

    def test_get_info_structure(self) -> None:
        info = SearchQAEvaluator(target=0.90).get_info()
        assert info["benchmark"] == "searchqa"
        assert info["target"] == 0.90
        assert isinstance(info["metrics"], list)
        assert "accuracy" in info["metrics"]

    def test_protocol_conformance(self) -> None:
        assert isinstance(SearchQAEvaluator(), Evaluator)

    def test_custom_target(self) -> None:
        ev = SearchQAEvaluator(target=0.95)
        assert ev.target == 0.95


class TestBuildSearchqaSurface:
    def test_no_skill(self) -> None:
        surface = build_searchqa_surface()
        assert isinstance(surface, Surface)
        assert surface.prompt_slots == {}

    def test_with_skill_path(self, tmp_path: Path) -> None:
        skill = tmp_path / "skill.md"
        skill.write_text("my prompt template")
        surface = build_searchqa_surface(skill_path=skill)
        assert surface.prompt_slots["skill"] == "my prompt template"

    def test_nonexistent_skill_path(self, tmp_path: Path) -> None:
        surface = build_searchqa_surface(skill_path=tmp_path / "missing.md")
        assert surface.prompt_slots == {}


class TestBuildSearchqaConfig:
    def test_defaults(self) -> None:
        config = build_searchqa_config()
        assert isinstance(config, LoopConfig)
        assert config.epochs == 3
        assert config.steps_per_epoch == 5

    def test_custom_values(self) -> None:
        config = build_searchqa_config(epochs=10, steps_per_epoch=20)
        assert config.epochs == 10
        assert config.steps_per_epoch == 20


class TestBuildSearchqaExecutor:
    def test_default(self) -> None:
        executor = build_searchqa_executor()
        assert isinstance(executor, HarborExecutor)
        assert executor.harbor_script == "./run-harbor.sh"

    def test_custom_script(self) -> None:
        executor = build_searchqa_executor(harbor_script="/opt/bench/run.sh")
        assert isinstance(executor, HarborExecutor)
        assert executor.harbor_script == "/opt/bench/run.sh"
