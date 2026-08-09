"""Tests for factory.cli.skillopt — skillopt CLI subcommand."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from factory.cli import build_parser
from factory.cli.skillopt import cmd_skillopt
from factory.optimization.loop import TrainResult
from factory.optimization.types import StepRecord


def _make_args(
    path: str = "/tmp/project",
    benchmark: str | None = None,
    skill_path: str | None = None,
    epochs: int = 1,
    steps_per_epoch: int = 1,
) -> argparse.Namespace:
    return argparse.Namespace(
        path=path,
        benchmark=benchmark,
        skill_path=skill_path,
        epochs=epochs,
        steps_per_epoch=steps_per_epoch,
    )


class TestSkilloptParsing:
    def test_parses_path(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["skillopt", "/some/path"])
        assert args.command == "skillopt"
        assert args.path == "/some/path"

    def test_parses_benchmark(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["skillopt", "/p", "--benchmark", "searchqa"])
        assert args.benchmark == "searchqa"

    def test_parses_skill_path(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["skillopt", "/p", "--skill-path", "/s/skill.md"])
        assert args.skill_path == "/s/skill.md"

    def test_parses_epochs(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["skillopt", "/p", "--epochs", "5"])
        assert args.epochs == 5

    def test_parses_steps_per_epoch(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["skillopt", "/p", "--steps-per-epoch", "10"])
        assert args.steps_per_epoch == 10

    def test_default_benchmark_is_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["skillopt", "/p"])
        assert args.benchmark is None

    def test_default_epochs(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["skillopt", "/p"])
        assert args.epochs == 1

    def test_default_steps_per_epoch(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["skillopt", "/p"])
        assert args.steps_per_epoch == 1


class TestSkilloptExecution:
    def test_nonexistent_path_returns_error(self) -> None:
        args = _make_args(path="/nonexistent/path/that/does/not/exist")
        rc = cmd_skillopt(args)
        assert rc == 1

    def test_default_benchmark(self, tmp_path: Path) -> None:
        fake_result = TrainResult(
            steps=[StepRecord(step_number=1)],
            best_score=0.75,
            best_step=1,
            final_score=0.70,
        )
        mock_loop = MagicMock()
        mock_loop.train.return_value = fake_result

        with patch("factory.optimization.OptimizationLoop", return_value=mock_loop) as mock_cls:
            rc = cmd_skillopt(_make_args(path=str(tmp_path)))

        assert rc == 0
        mock_loop.train.assert_called_once()
        call_kwargs = mock_cls.call_args
        from factory.optimization.executors import FactoryCeoExecutor
        from factory.inner_loop import CirclePackingEvaluator
        assert isinstance(call_kwargs.kwargs["executor"], FactoryCeoExecutor)
        assert isinstance(call_kwargs.kwargs["evaluator"], CirclePackingEvaluator)

    def test_searchqa_benchmark(self, tmp_path: Path) -> None:
        fake_result = TrainResult(
            steps=[StepRecord(step_number=1)],
            best_score=0.85,
            best_step=1,
            final_score=0.80,
        )
        mock_loop = MagicMock()
        mock_loop.train.return_value = fake_result

        with (
            patch("factory.optimization.OptimizationLoop", return_value=mock_loop) as mock_cls,
            patch(
                "factory.optimization.benchmarks.searchqa.build_searchqa_executor",
            ) as mock_build_exec,
        ):
            mock_executor = MagicMock()
            mock_build_exec.return_value = mock_executor
            rc = cmd_skillopt(_make_args(path=str(tmp_path), benchmark="searchqa"))

        assert rc == 0
        mock_build_exec.assert_called_once()
        from factory.optimization.benchmarks.searchqa import SearchQAEvaluator
        assert isinstance(mock_cls.call_args.kwargs["evaluator"], SearchQAEvaluator)

    def test_skill_path_loading(self, tmp_path: Path) -> None:
        skill = tmp_path / "skill.md"
        skill.write_text("optimize this prompt")

        fake_result = TrainResult(steps=[], best_score=0.0, best_step=0, final_score=0.0)
        mock_loop = MagicMock()
        mock_loop.train.return_value = fake_result

        with patch("factory.optimization.OptimizationLoop", return_value=mock_loop) as mock_cls:
            cmd_skillopt(_make_args(path=str(tmp_path), skill_path=str(skill)))

        surface = mock_cls.call_args.kwargs["surface"]
        assert surface.prompt_slots["skill"] == "optimize this prompt"

    def test_nonexistent_skill_path_ignored(self, tmp_path: Path) -> None:
        fake_result = TrainResult(steps=[], best_score=0.0, best_step=0, final_score=0.0)
        mock_loop = MagicMock()
        mock_loop.train.return_value = fake_result

        with patch("factory.optimization.OptimizationLoop", return_value=mock_loop) as mock_cls:
            cmd_skillopt(_make_args(path=str(tmp_path), skill_path="/no/such/skill.md"))

        surface = mock_cls.call_args.kwargs["surface"]
        assert surface.prompt_slots == {}

    def test_config_passed_correctly(self, tmp_path: Path) -> None:
        fake_result = TrainResult(steps=[], best_score=0.0, best_step=0, final_score=0.0)
        mock_loop = MagicMock()
        mock_loop.train.return_value = fake_result

        with patch("factory.optimization.OptimizationLoop", return_value=mock_loop) as mock_cls:
            cmd_skillopt(_make_args(path=str(tmp_path), epochs=3, steps_per_epoch=7))

        config = mock_cls.call_args.kwargs["config"]
        assert config.epochs == 3
        assert config.steps_per_epoch == 7

    def test_output_format(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        fake_result = TrainResult(
            steps=[StepRecord(step_number=1), StepRecord(step_number=2)],
            best_score=0.9123,
            best_step=2,
            final_score=0.8567,
        )
        mock_loop = MagicMock()
        mock_loop.train.return_value = fake_result

        with patch("factory.optimization.OptimizationLoop", return_value=mock_loop):
            cmd_skillopt(_make_args(path=str(tmp_path)))

        captured = capsys.readouterr()
        assert "2 steps" in captured.out
        assert "0.9123" in captured.out
        assert "step 2" in captured.out
        assert "0.8567" in captured.out
