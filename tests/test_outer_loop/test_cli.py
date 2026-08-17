"""Tests for outer-loop CLI argument parsing and mode registration."""

from __future__ import annotations

from unittest.mock import patch


class TestDiskSpaceCheck:
    def test_sufficient_space_passes(self, tmp_path: object) -> None:
        from pathlib import Path

        from factory.cli.outer_loop import _check_disk_space

        assert _check_disk_space(Path(str(tmp_path)), population_size=4) is True

    def test_insufficient_space_fails(self, tmp_path: object) -> None:
        from collections import namedtuple
        from pathlib import Path

        from factory.cli.outer_loop import _check_disk_space

        DiskUsage = namedtuple("usage", ["total", "used", "free"])
        tiny = DiskUsage(total=100 * 1024**3, used=99 * 1024**3, free=1 * 1024**3)
        with patch("factory.cli.outer_loop.shutil.disk_usage", return_value=tiny):
            assert _check_disk_space(Path(str(tmp_path)), population_size=4) is False

    def test_required_space_formula(self) -> None:
        from collections import namedtuple
        from pathlib import Path

        from factory.cli.outer_loop import _check_disk_space

        DiskUsage = namedtuple("usage", ["total", "used", "free"])

        exactly_enough = DiskUsage(
            total=100 * 1024**3,
            used=80 * 1024**3,
            free=int(20.1 * 1024**3),
        )
        with patch("factory.cli.outer_loop.shutil.disk_usage", return_value=exactly_enough):
            assert _check_disk_space(Path("/tmp"), population_size=50) is True

        not_enough = DiskUsage(
            total=100 * 1024**3,
            used=81 * 1024**3,
            free=int(19.9 * 1024**3),
        )
        with patch("factory.cli.outer_loop.shutil.disk_usage", return_value=not_enough):
            assert _check_disk_space(Path("/tmp"), population_size=50) is False


class TestOuterLoopModeRegistration:
    def test_outer_loop_in_ceo_modes(self) -> None:
        from factory.cli._helpers import CEO_MODES

        assert "outer-loop" in CEO_MODES


class TestOuterLoopCLIParsing:
    def _parse_outer_loop(self, *args: str) -> object:
        from factory.cli._main import build_parser

        parser = build_parser()
        return parser.parse_args(["outer-loop", *args])

    def test_calibrate_subcommand(self) -> None:
        ns = self._parse_outer_loop("calibrate", "/tmp/project")
        assert ns.command == "outer-loop"
        assert ns.outer_loop_command == "calibrate"
        assert ns.project_path == "/tmp/project"

    def test_calibrate_with_options(self) -> None:
        ns = self._parse_outer_loop(
            "calibrate", "/tmp/project",
            "--benchmark", "featurebench",
            "--budget", "50",
            "--population-size", "4",
        )
        assert ns.benchmark == "featurebench"
        assert ns.budget == 50
        assert ns.population_size == 4

    def test_calibrate_with_target_project(self) -> None:
        ns = self._parse_outer_loop(
            "calibrate", "/tmp/project",
            "--project-dir", "/tmp/featurebench-instance",
        )
        assert ns.project_dir == "/tmp/featurebench-instance"

    def test_calibrate_without_target_project(self) -> None:
        ns = self._parse_outer_loop("calibrate", "/tmp/project")
        assert ns.project_dir is None

    def test_evaluate_subcommand(self) -> None:
        ns = self._parse_outer_loop("evaluate", "/tmp/project", "--generation", "3")
        assert ns.outer_loop_command == "evaluate"
        assert ns.generation == 3

    def test_reflect_subcommand(self) -> None:
        ns = self._parse_outer_loop("reflect", "/tmp/project", "--generation", "2")
        assert ns.outer_loop_command == "reflect"
        assert ns.generation == 2

    def test_evolve_subcommand(self) -> None:
        ns = self._parse_outer_loop("evolve", "/tmp/project", "--generation", "1")
        assert ns.outer_loop_command == "evolve"
        assert ns.generation == 1

    def test_status_subcommand(self) -> None:
        ns = self._parse_outer_loop("status", "/tmp/project")
        assert ns.outer_loop_command == "status"

    def test_status_check_converge(self) -> None:
        ns = self._parse_outer_loop("status", "/tmp/project", "--check-converge")
        assert ns.check_converge is True

    def test_promote_subcommand(self) -> None:
        ns = self._parse_outer_loop("promote", "/tmp/project", "--mode-name", "evolve-gen5-abc")
        assert ns.outer_loop_command == "promote"
        assert ns.mode_name == "evolve-gen5-abc"

    def test_promote_with_permanent_name(self) -> None:
        ns = self._parse_outer_loop(
            "promote", "/tmp/project",
            "--mode-name", "evolve-gen5-abc",
            "--permanent-name", "my-evolved",
        )
        assert ns.permanent_name == "my-evolved"


class TestEvaluateTargetProjectFallback:
    def test_evaluate_uses_config_target_project(self, tmp_path: object) -> None:
        """_cmd_evaluate falls back to config.target_project when --project-dir not passed."""
        import argparse
        from pathlib import Path
        from unittest.mock import patch

        from factory.outer_loop.models import SwarmConfig

        project = Path(str(tmp_path)) / "factory-project"
        project.mkdir()

        cfg = SwarmConfig(
            benchmark="featurebench",
            budget=50,
            target_project="/tmp/featurebench-instance",
        )

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch("factory.outer_loop.filesystem.load_checkpoint", return_value=None), \
             patch("factory.outer_loop.filesystem.save_checkpoint"):
            from factory.outer_loop.mode_registry import EphemeralModeRegistry

            with patch.object(EphemeralModeRegistry, "list_modes", return_value=[]):
                from factory.cli.outer_loop import _cmd_evaluate

                ns = argparse.Namespace(
                    project_path=str(project),
                    generation=0,
                    project_dir=None,
                )
                rc = _cmd_evaluate(ns)
                assert rc == 1  # no modes, but it should reach the "no modes" error


class TestOuterLoopWorkflowGraph:
    def test_workflow_validates(self) -> None:
        from factory.workflow.contributed.outer_loop.workflow import workflow

        wf = workflow()
        issues = wf.validate_graph()
        assert issues == [], f"Workflow validation issues: {issues}"

    def test_workflow_name(self) -> None:
        from factory.workflow.contributed.outer_loop.workflow import workflow

        wf = workflow()
        assert wf.name == "outer-loop"

    def test_workflow_start_node(self) -> None:
        from factory.workflow.contributed.outer_loop.workflow import workflow

        wf = workflow()
        assert wf.start_node == "seed"

    def test_workflow_has_expected_nodes(self) -> None:
        from factory.workflow.contributed.outer_loop.workflow import workflow

        wf = workflow()
        expected = {"seed", "evaluate", "reflect", "evolve", "gate_converge"}
        assert set(wf.nodes.keys()) == expected

    def test_workflow_generation_loop(self) -> None:
        from factory.workflow.contributed.outer_loop.workflow import workflow
        from factory.workflow.primitives import VerdictType

        wf = workflow()
        loop_edge = [
            e for e in wf.edges
            if e.source == "gate_converge"
            and e.target == "evaluate"
            and e.condition == VerdictType.RELOOP
        ]
        assert len(loop_edge) == 1

    def test_workflow_is_terminal(self) -> None:
        from factory.workflow.contributed.outer_loop.workflow import workflow

        wf = workflow()
        assert wf.terminal is True

    def test_workflow_serialization_round_trip(self) -> None:
        from factory.workflow.contributed.outer_loop.workflow import workflow
        from factory.workflow.primitives import Workflow

        wf = workflow()
        data = wf.to_dict()
        restored = Workflow.from_dict(data)

        assert restored.name == wf.name
        assert set(restored.nodes.keys()) == set(wf.nodes.keys())
        assert len(restored.edges) == len(wf.edges)
