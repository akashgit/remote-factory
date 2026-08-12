"""Tests for factory.cli.optimize — the optimize CLI command."""

from __future__ import annotations

import argparse
from unittest.mock import patch

from factory.cli._main import build_parser, _COMMAND_GROUPS, _REFACTORY_AGENT_COMMANDS
from factory.cli.optimize import cmd_optimize


class TestOptimizeArgumentParsing:
    """Verify the argparse setup for the optimize subcommand."""

    def test_optimize_parser_exists(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["optimize", "/tmp/test-project"])
        assert args.command == "optimize"
        assert args.path == "/tmp/test-project"

    def test_default_values(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["optimize", "/tmp/test-project"])
        assert args.benchmark == "searchqa"
        assert args.skill_path is None
        assert args.steps == 3
        assert args.epochs == 1
        assert args.concurrency == 5
        assert args.git_ref is None
        assert args.docker_host is None
        assert args.model == "sonnet"

    def test_custom_values(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "optimize", "/tmp/proj",
            "--benchmark", "searchqa",
            "--skill-path", "/tmp/skill.md",
            "--steps", "5",
            "--epochs", "2",
            "--concurrency", "10",
            "--git-ref", "feat/branch",
            "--docker-host", "unix:///run/podman.sock",
            "--model", "opus",
        ])
        assert args.benchmark == "searchqa"
        assert args.skill_path == "/tmp/skill.md"
        assert args.steps == 5
        assert args.epochs == 2
        assert args.concurrency == 10
        assert args.git_ref == "feat/branch"
        assert args.docker_host == "unix:///run/podman.sock"
        assert args.model == "opus"


class TestOptimizeCommandWiring:
    """Verify optimize is registered in all three wiring locations."""

    def test_in_handler_dict(self) -> None:
        import factory.cli as _cli
        assert hasattr(_cli, "cmd_optimize")

    def test_in_command_groups(self) -> None:
        group_dict = dict(_COMMAND_GROUPS)
        assert "optimize" in group_dict["Self-Evolution"]

    def test_in_refactory_agent_commands(self) -> None:
        assert "optimize" in _REFACTORY_AGENT_COMMANDS


class TestOptimizeDynamicArgs:
    """Verify --benchmark auto and --benchmark-dir argparse setup."""

    def test_benchmark_auto_choice(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["optimize", "/tmp/proj", "--benchmark", "auto"])
        assert args.benchmark == "auto"

    def test_benchmark_dir_arg(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "optimize", "/tmp/proj", "--benchmark-dir", "/some/path",
        ])
        assert args.benchmark_dir == "/some/path"

    def test_benchmark_auto_no_dir_errors(self, tmp_path) -> None:
        args = argparse.Namespace(
            path=str(tmp_path),
            benchmark="auto",
            benchmark_dir=None,
            skill_path=None,
            steps=1,
            epochs=1,
            concurrency=5,
            git_ref=None,
            docker_host=None,
            model="sonnet",
            split_seed=42,
            splits_dir=None,
            legacy=True,
        )
        result = cmd_optimize(args)
        assert result == 1


class TestLegacyFlag:
    """Verify --legacy flag routing."""

    def test_legacy_flag_parsed(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["optimize", "/tmp/proj", "--legacy"])
        assert args.legacy is True

    def test_legacy_flag_default_false(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["optimize", "/tmp/proj"])
        assert args.legacy is False


class TestCmdOptimize:
    """Test cmd_optimize with mocked dependencies."""

    def test_missing_path_returns_1(self, tmp_path) -> None:
        args = argparse.Namespace(
            path=str(tmp_path / "nonexistent"),
            benchmark="searchqa",
            skill_path=None,
            steps=1,
            epochs=1,
            concurrency=5,
            git_ref=None,
            docker_host=None,
            model="sonnet",
            legacy=False,
        )
        result = cmd_optimize(args)
        assert result == 1

    def test_legacy_invalid_benchmark_returns_1(self, tmp_path) -> None:
        args = argparse.Namespace(
            path=str(tmp_path),
            benchmark="invalid_benchmark",
            benchmark_dir=None,
            skill_path=None,
            steps=1,
            epochs=1,
            concurrency=5,
            git_ref=None,
            docker_host=None,
            model="sonnet",
            split_seed=42,
            splits_dir=None,
            legacy=True,
        )
        result = cmd_optimize(args)
        assert result == 1

    def test_legacy_successful_run_returns_0(self, tmp_path) -> None:
        from factory.optimization.loop import TrainResult
        from factory.optimization.types import StepRecord

        mock_result = TrainResult(
            steps=[
                StepRecord(step_number=1, score_start=0.0, score_end=0.5, score_delta=0.5, verdict="keep"),
                StepRecord(step_number=2, score_start=0.5, score_end=0.7, score_delta=0.2, verdict="keep"),
            ],
            best_score=0.7,
            best_step=2,
            final_score=0.7,
        )

        args = argparse.Namespace(
            path=str(tmp_path),
            benchmark="searchqa",
            benchmark_dir=None,
            skill_path=None,
            steps=2,
            epochs=1,
            concurrency=5,
            git_ref="main",
            docker_host=None,
            model="sonnet",
            split_seed=42,
            splits_dir=None,
            legacy=True,
        )

        with patch("factory.optimization.OptimizationLoop") as mock_loop_cls, \
             patch("factory.optimization.benchmarks.harbor.HarborBenchmark"), \
             patch("factory.optimization.benchmarks.searchqa.SearchQAEvaluator"), \
             patch("factory.optimization.mutators.agentic.AgenticMutator"):
            mock_loop_cls.return_value.train.return_value = mock_result
            result = cmd_optimize(args)

        assert result == 0

    def test_workflow_mode_writes_state(self, tmp_path) -> None:
        """Non-legacy mode writes initial state files and calls workflow."""
        args = argparse.Namespace(
            path=str(tmp_path),
            benchmark="searchqa",
            skill_path=None,
            steps=1,
            concurrency=5,
            git_ref="main",
            docker_host=None,
            model="sonnet",
            legacy=False,
        )

        import json

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            # Pre-write state so summary reads succeed
            opt_dir = tmp_path / ".factory" / "optimization"
            result = cmd_optimize(args)
            assert (opt_dir / "current_skill.md").exists()
            state = json.loads((opt_dir / "state.json").read_text())
            assert state["step"] == 0
            assert state["history"] == []

    def test_legacy_skill_path_loaded(self, tmp_path) -> None:
        from factory.optimization.loop import TrainResult

        skill_file = tmp_path / "skill.md"
        skill_file.write_text("Test skill content")

        mock_result = TrainResult(steps=[], best_score=0.0, best_step=0, final_score=0.0)

        args = argparse.Namespace(
            path=str(tmp_path),
            benchmark="searchqa",
            benchmark_dir=None,
            skill_path=str(skill_file),
            steps=1,
            epochs=1,
            concurrency=5,
            git_ref="main",
            docker_host=None,
            model="sonnet",
            split_seed=42,
            splits_dir=None,
            legacy=True,
        )

        with patch("factory.optimization.OptimizationLoop") as mock_loop_cls, \
             patch("factory.optimization.benchmarks.harbor.HarborBenchmark"), \
             patch("factory.optimization.benchmarks.searchqa.SearchQAEvaluator"), \
             patch("factory.optimization.mutators.agentic.AgenticMutator"):
            mock_loop_cls.return_value.train.return_value = mock_result
            result = cmd_optimize(args)

        assert result == 0
