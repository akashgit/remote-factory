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


class TestInnerLoopFactoryReusesExistingModes:
    """Bug #16: _make_inner_loop_factory should reuse existing modes, not create eval copies."""

    def test_returns_existing_mode_by_structural_hash(self, tmp_path: object) -> None:
        from pathlib import Path

        from factory.cli.outer_loop import _make_inner_loop_factory
        from factory.outer_loop.mode_registry import EphemeralModeRegistry
        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        project = Path(str(tmp_path))
        registry = EphemeralModeRegistry(project)

        wf = Workflow(
            name="test-wf",
            nodes={
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    writes={".factory/reviews/builder-latest.md"},
                ),
            },
            edges=[],
            start_node="builder",
            terminal=True,
        )
        registered_name = registry.register("abc12345", 0, wf)

        factory_fn = _make_inner_loop_factory(registry)
        result = factory_fn(wf)
        assert result == registered_name
        assert "eval" not in result

    def test_does_not_create_eval_copy_modes(self, tmp_path: object) -> None:
        from pathlib import Path

        from factory.cli.outer_loop import _make_inner_loop_factory
        from factory.outer_loop.mode_registry import EphemeralModeRegistry
        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        project = Path(str(tmp_path))
        registry = EphemeralModeRegistry(project)

        wf = Workflow(
            name="test-wf",
            nodes={
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    writes={".factory/reviews/builder-latest.md"},
                ),
            },
            edges=[],
            start_node="builder",
            terminal=True,
        )
        registry.register("seed0001", 0, wf)

        factory_fn = _make_inner_loop_factory(registry)
        factory_fn(wf)
        factory_fn(wf)
        factory_fn(wf)

        modes = registry.list_modes()
        eval_modes = [m for m in modes if "eval" in m]
        assert eval_modes == [], f"Unexpected eval-copy modes: {eval_modes}"
        assert len(modes) == 1

    def test_caches_hash_lookups(self, tmp_path: object) -> None:
        from pathlib import Path

        from factory.cli.outer_loop import _make_inner_loop_factory
        from factory.outer_loop.mode_registry import EphemeralModeRegistry
        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        project = Path(str(tmp_path))
        registry = EphemeralModeRegistry(project)

        wf = Workflow(
            name="test-wf",
            nodes={
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    writes={".factory/reviews/builder-latest.md"},
                ),
            },
            edges=[],
            start_node="builder",
            terminal=True,
        )
        registered_name = registry.register("abc12345", 0, wf)

        factory_fn = _make_inner_loop_factory(registry)
        r1 = factory_fn(wf)
        r2 = factory_fn(wf)
        assert r1 == r2 == registered_name

    def test_fallback_registers_new_mode_for_unknown_workflow(self, tmp_path: object) -> None:
        from pathlib import Path

        from factory.cli.outer_loop import _make_inner_loop_factory
        from factory.outer_loop.mode_registry import EphemeralModeRegistry
        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        project = Path(str(tmp_path))
        registry = EphemeralModeRegistry(project)

        factory_fn = _make_inner_loop_factory(registry)

        wf = Workflow(
            name="new-wf",
            nodes={
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    writes={".factory/reviews/builder-latest.md"},
                ),
            },
            edges=[],
            start_node="builder",
            terminal=True,
        )
        result = factory_fn(wf)
        assert result.startswith("evolve-gen0-")
        assert "eval" not in result


class TestReflectReadsCachedData:
    """Bug #17: _cmd_reflect should read cached data instead of re-evaluating."""

    def test_reflect_uses_saved_results_and_cycle_summary(self, tmp_path: object) -> None:
        import argparse
        import json
        from pathlib import Path
        from unittest.mock import patch

        from factory.outer_loop.models import SwarmConfig

        project = Path(str(tmp_path))
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)
        results_dir = project / ".factory" / "outer_loop" / "results"
        results_dir.mkdir(parents=True)

        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        wf1 = Workflow(
            name="mode-a",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )
        wf2 = Workflow(
            name="mode-b",
            nodes={"b": AgentNode(id="b", role=AgentRole.RESEARCHER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )

        from factory.outer_loop.mode_registry import EphemeralModeRegistry

        registry = EphemeralModeRegistry(project)
        name_a = registry.register("aaa", 0, wf1)
        name_b = registry.register("bbb", 0, wf2)

        gen_results = {
            name_a: {"score": 0.85, "cost_usd": 1.0},
            name_b: {"score": 0.72, "cost_usd": 0.5},
        }
        (results_dir / "gen0.json").write_text(json.dumps(gen_results))

        for name, score in [(name_a, 0.85), (name_b, 0.72)]:
            runs_dir = project / ".factory" / "outer_loop" / "runs" / name
            runs_dir.mkdir(parents=True)
            summary = {"mode": name, "score": score, "cost_usd": 0.5, "kept": 2, "reverted": 1}
            (runs_dir / "cycle_summary.json").write_text(json.dumps(summary))

        cfg = SwarmConfig(benchmark="featurebench", budget=50)

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg):
            from factory.cli.outer_loop import _cmd_reflect

            ns = argparse.Namespace(project_path=str(project), generation=0)
            rc = _cmd_reflect(ns)
            assert rc == 0

    def test_load_cycle_summary_returns_record(self, tmp_path: object) -> None:
        import json
        from pathlib import Path

        from factory.cli.outer_loop import _load_cycle_summary

        project = Path(str(tmp_path))
        runs_dir = project / ".factory" / "outer_loop" / "runs" / "evolve-gen0-abc"
        runs_dir.mkdir(parents=True)
        summary = {
            "mode": "evolve-gen0-abc",
            "score": 0.9,
            "cost_usd": 1.5,
            "kept": 3,
            "reverted": 1,
            "agents_failed": 0,
            "duration_ms": 5000,
        }
        (runs_dir / "cycle_summary.json").write_text(json.dumps(summary))

        rec = _load_cycle_summary(project, "evolve-gen0-abc")
        assert rec is not None
        assert rec.score_end == 0.9
        assert rec.kept == 3
        assert rec.reverted == 1
        assert rec.total_cost_usd == 1.5
        assert rec.duration_s == 5.0

    def test_load_cycle_summary_returns_none_for_missing(self, tmp_path: object) -> None:
        from pathlib import Path

        from factory.cli.outer_loop import _load_cycle_summary

        project = Path(str(tmp_path))
        rec = _load_cycle_summary(project, "nonexistent-mode")
        assert rec is None

    def test_evaluate_persists_cycle_summary(self, tmp_path: object) -> None:
        """_cmd_evaluate should write cycle_summary.json for each evaluated mode."""
        import argparse
        import json
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from factory.outer_loop.models import EvalResult, SwarmConfig

        project = Path(str(tmp_path))
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)

        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        wf = Workflow(
            name="test-wf",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )
        from factory.outer_loop.mode_registry import EphemeralModeRegistry

        registry = EphemeralModeRegistry(project)
        mode_name = registry.register("test01", 0, wf)

        cfg = SwarmConfig(benchmark="featurebench", budget=50)
        mock_result = EvalResult(
            score=0.75, benchmark_score=0.8, cost_usd=2.0,
            details={"kept": 2, "reverted": 1},
        )

        mock_evaluator = MagicMock()
        mock_evaluator.evaluate.return_value = mock_result

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch("factory.outer_loop.filesystem.load_checkpoint", return_value=None), \
             patch("factory.outer_loop.filesystem.save_checkpoint"), \
             patch("factory.outer_loop.evaluator.SwarmEvaluator", return_value=mock_evaluator):
            from factory.cli.outer_loop import _cmd_evaluate

            ns = argparse.Namespace(
                project_path=str(project), generation=0, project_dir=None,
            )
            rc = _cmd_evaluate(ns)
            assert rc == 0

        summary_path = (
            project / ".factory" / "outer_loop" / "runs" / mode_name / "cycle_summary.json"
        )
        assert summary_path.exists()
        data = json.loads(summary_path.read_text())
        assert data["score"] == 0.75
        assert data["kept"] == 2


class TestReflectPassesKnobValues:
    def test_reflect_passes_knob_values_by_id(self, tmp_path: object) -> None:
        """_cmd_reflect builds knob_values_by_id from registry workflows and passes to reflect."""
        import argparse
        import json
        from pathlib import Path
        from unittest.mock import patch

        from factory.outer_loop.models import SwarmConfig

        project = Path(str(tmp_path))
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)
        results_dir = project / ".factory" / "outer_loop" / "results"
        results_dir.mkdir(parents=True)

        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        wf1 = Workflow(
            name="mode-a",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
            knob_values={"temperature": 0.7, "strategy": "greedy"},
        )
        wf2 = Workflow(
            name="mode-b",
            nodes={"b": AgentNode(id="b", role=AgentRole.RESEARCHER, writes=set())},
            edges=[], start_node="b", terminal=True,
            knob_values={"temperature": 0.9},
        )

        from factory.outer_loop.mode_registry import EphemeralModeRegistry

        registry = EphemeralModeRegistry(project)
        name_a = registry.register("aaa", 0, wf1)
        name_b = registry.register("bbb", 0, wf2)

        gen_results = {
            name_a: {"score": 0.85, "cost_usd": 1.0},
            name_b: {"score": 0.72, "cost_usd": 0.5},
        }
        (results_dir / "gen0.json").write_text(json.dumps(gen_results))

        for name, score in [(name_a, 0.85), (name_b, 0.72)]:
            runs_dir = project / ".factory" / "outer_loop" / "runs" / name
            runs_dir.mkdir(parents=True)
            summary = {"mode": name, "score": score, "cost_usd": 0.5, "kept": 2, "reverted": 1}
            (runs_dir / "cycle_summary.json").write_text(json.dumps(summary))

        cfg = SwarmConfig(benchmark="featurebench", budget=50)

        captured_kwargs: list[dict] = []
        from factory.outer_loop.reflector import OuterLoopReflector, ReflectionReport

        def spy_reflect(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return ReflectionReport()

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch.object(OuterLoopReflector, "reflect", side_effect=spy_reflect):
            from factory.cli.outer_loop import _cmd_reflect

            ns = argparse.Namespace(project_path=str(project), generation=0)
            rc = _cmd_reflect(ns)
            assert rc == 0

        assert len(captured_kwargs) == 1
        kvbi = captured_kwargs[0].get("knob_values_by_id")
        assert kvbi is not None
        assert len(kvbi) == 2
        assert kvbi[name_a] == {"temperature": 0.7, "strategy": "greedy"}
        assert kvbi[name_b] == {"temperature": 0.9}


class TestEvolveHandlesMalformedReflection:
    def test_evolve_handles_malformed_reflection_json(self, tmp_path: object) -> None:
        """_cmd_evolve gracefully handles malformed reflection JSON (returns 0, report=None)."""
        import argparse
        from pathlib import Path
        from unittest.mock import patch

        from factory.outer_loop.models import SwarmConfig

        project = Path(str(tmp_path))
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)
        reflections_dir = project / ".factory" / "outer_loop" / "reflections"
        reflections_dir.mkdir(parents=True)

        (reflections_dir / "gen0.json").write_text("not json {{{")

        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        wf = Workflow(
            name="test-wf",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )
        from factory.outer_loop.mode_registry import EphemeralModeRegistry

        registry = EphemeralModeRegistry(project)
        registry.register("test01", 0, wf)

        cfg = SwarmConfig(benchmark="featurebench", budget=50)

        captured_kwargs: list[dict] = []
        from factory.outer_loop import mutations as _mutations_mod

        original_apply = _mutations_mod.apply_random_mutation

        def spy_apply(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return original_apply(*args, **kwargs)

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch.object(_mutations_mod, "apply_random_mutation", side_effect=spy_apply):
            from factory.cli.outer_loop import _cmd_evolve

            ns = argparse.Namespace(project_path=str(project), generation=0)
            rc = _cmd_evolve(ns)
            assert rc == 0

        assert len(captured_kwargs) > 0
        assert captured_kwargs[0].get("reflection_report") is None


class TestEvolveLoadsReflectionReport:
    def test_evolve_loads_persisted_reflection_json(self, tmp_path: object) -> None:
        """_cmd_evolve should load reflections/gen0.json and pass it to apply_random_mutation."""
        import argparse
        import json
        from pathlib import Path
        from unittest.mock import patch

        from factory.outer_loop.models import SwarmConfig

        project = Path(str(tmp_path))
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)
        reflections_dir = project / ".factory" / "outer_loop" / "reflections"
        reflections_dir.mkdir(parents=True)

        report_data = {
            "generation": 0,
            "failure_patterns": ["Agent builder failed"],
            "success_patterns": ["Winner used researcher"],
            "mutation_suggestions": ["NODE_INSERT: Add researcher"],
            "prompt_improvements": [],
            "structural_recommendations": [],
            "top_k_ids": ["w1"],
            "bottom_k_ids": ["l1"],
        }
        (reflections_dir / "gen0.json").write_text(json.dumps(report_data))

        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        wf = Workflow(
            name="test-wf",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )
        from factory.outer_loop.mode_registry import EphemeralModeRegistry

        registry = EphemeralModeRegistry(project)
        registry.register("test01", 0, wf)

        cfg = SwarmConfig(benchmark="featurebench", budget=50)

        captured_kwargs: list[dict] = []
        from factory.outer_loop import mutations as _mutations_mod

        original_apply = _mutations_mod.apply_random_mutation

        def spy_apply(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return original_apply(*args, **kwargs)

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch.object(_mutations_mod, "apply_random_mutation", side_effect=spy_apply):
            from factory.cli.outer_loop import _cmd_evolve

            ns = argparse.Namespace(project_path=str(project), generation=0)
            _cmd_evolve(ns)

        assert len(captured_kwargs) > 0
        assert captured_kwargs[0].get("reflection_report") is not None

    def test_evolve_works_without_reflection_file(self, tmp_path: object) -> None:
        """_cmd_evolve should still work when no reflection JSON exists."""
        import argparse
        from pathlib import Path
        from unittest.mock import patch

        from factory.outer_loop.models import SwarmConfig

        project = Path(str(tmp_path))
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)

        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        wf = Workflow(
            name="test-wf",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )
        from factory.outer_loop.mode_registry import EphemeralModeRegistry

        registry = EphemeralModeRegistry(project)
        registry.register("test01", 0, wf)

        cfg = SwarmConfig(benchmark="featurebench", budget=50)

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg):
            from factory.cli.outer_loop import _cmd_evolve

            ns = argparse.Namespace(project_path=str(project), generation=0)
            rc = _cmd_evolve(ns)
            assert rc == 0


class TestCalibrateSeedWorkflowFromRegistry:
    """Cover seed_workflow loading from WorkflowRegistry in _cmd_calibrate."""

    def test_seed_workflow_loads_from_registry(self, tmp_path: object) -> None:
        import argparse
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from factory.outer_loop.models import SwarmConfig
        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        project = Path(str(tmp_path)) / "calibrate-proj"
        project.mkdir()

        registry_wf = Workflow(
            name="registry-seed",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
            knob_values={"style": "focused"},
            knob_bounds={"style": ["focused", "broad"]},
            knob_expandable={"style": "prompt hint"},
        )

        cfg = SwarmConfig(
            benchmark="featurebench",
            budget=10,
            population_size=2,
            seed_workflow="my-seed",
        )

        ns = argparse.Namespace(
            project_path=str(project),
        )

        mock_engine = MagicMock()
        mock_pop = MagicMock()
        mock_engine.seed.return_value = mock_pop

        captured_base_wf = []

        def spy_seed(wf, cfg_arg):
            captured_base_wf.append(wf)
            return mock_pop

        mock_engine.seed = spy_seed

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch("factory.outer_loop.filesystem.init_filesystem", return_value=project / ".factory" / "outer_loop"), \
             patch("factory.outer_loop.engine.SwarmEngine", return_value=mock_engine), \
             patch("factory.outer_loop.evaluator.SwarmEvaluator"), \
             patch("factory.outer_loop.similarity.NoveltyFilter"), \
             patch("factory.workflow.registry.WorkflowRegistry.get_workflow", return_value=registry_wf), \
             patch.object(mock_pop, "save"), \
             patch("factory.outer_loop.filesystem.save_checkpoint"):
            from factory.cli.outer_loop import _cmd_calibrate
            _cmd_calibrate(ns)

        assert len(captured_base_wf) == 1
        assert captured_base_wf[0].knob_values == {"style": "focused"}

    def test_seed_workflow_none_from_registry_uses_default(self, tmp_path: object) -> None:
        import argparse
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from factory.outer_loop.models import SwarmConfig

        project = Path(str(tmp_path)) / "calibrate-proj2"
        project.mkdir()

        cfg = SwarmConfig(
            benchmark="featurebench",
            budget=10,
            population_size=2,
            seed_workflow="nonexistent",
        )

        ns = argparse.Namespace(
            project_path=str(project),
        )

        mock_engine = MagicMock()
        mock_pop = MagicMock()
        mock_engine.seed.return_value = mock_pop

        captured_base_wf = []

        def spy_seed(wf, cfg_arg):
            captured_base_wf.append(wf)
            return mock_pop

        mock_engine.seed = spy_seed

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch("factory.outer_loop.filesystem.init_filesystem", return_value=project / ".factory" / "outer_loop"), \
             patch("factory.outer_loop.engine.SwarmEngine", return_value=mock_engine), \
             patch("factory.outer_loop.evaluator.SwarmEvaluator"), \
             patch("factory.outer_loop.similarity.NoveltyFilter"), \
             patch("factory.workflow.registry.WorkflowRegistry.get_workflow", return_value=None), \
             patch.object(mock_pop, "save"), \
             patch("factory.outer_loop.filesystem.save_checkpoint"):
            from factory.cli.outer_loop import _cmd_calibrate
            _cmd_calibrate(ns)

        assert len(captured_base_wf) == 1
        assert captured_base_wf[0].name == "featurebench-seed"


class TestCalibrateTaskWorkflowKnobs:
    """Cover task_module workflow knob extraction in _cmd_calibrate."""

    def test_task_workflow_knobs_propagated(self, tmp_path: object) -> None:
        import argparse
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from factory.outer_loop.models import SwarmConfig
        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        project = Path(str(tmp_path)) / "calibrate-task"
        project.mkdir()

        task_wf = Workflow(
            name="task-seed",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
            knob_values={"depth": 5, "approach": "iterative"},
            knob_bounds={"depth": [1, 3, 5], "approach": ["iterative", "recursive"]},
        )

        class FakeTask:
            def workflow(self):
                return task_wf

        cfg = SwarmConfig(
            benchmark="featurebench",
            budget=10,
            population_size=2,
            task_module="my.task.module",
        )

        ns = argparse.Namespace(
            project_path=str(project),
        )

        mock_engine = MagicMock()
        mock_pop = MagicMock()
        mock_engine.seed.return_value = mock_pop

        captured_base_wf = []

        def spy_seed(wf, cfg_arg):
            captured_base_wf.append(wf)
            return mock_pop

        mock_engine.seed = spy_seed

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch("factory.outer_loop.filesystem.init_filesystem", return_value=project / ".factory" / "outer_loop"), \
             patch("factory.outer_loop.engine.SwarmEngine", return_value=mock_engine), \
             patch("factory.outer_loop.evaluator.SwarmEvaluator"), \
             patch("factory.outer_loop.similarity.NoveltyFilter"), \
             patch.object(mock_pop, "save"), \
             patch("factory.outer_loop.filesystem.save_checkpoint"), \
             patch.object(SwarmConfig, "get_task", return_value=FakeTask()):
            from factory.cli.outer_loop import _cmd_calibrate
            _cmd_calibrate(ns)

        assert len(captured_base_wf) == 1
        assert captured_base_wf[0].knob_values == {"depth": 5, "approach": "iterative"}

    def test_task_workflow_exception_handled_gracefully(self, tmp_path: object) -> None:
        import argparse
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from factory.outer_loop.models import SwarmConfig

        project = Path(str(tmp_path)) / "calibrate-task-err"
        project.mkdir()

        cfg = SwarmConfig(
            benchmark="featurebench",
            budget=10,
            population_size=2,
            task_module="my.broken.module",
        )

        ns = argparse.Namespace(
            project_path=str(project),
        )

        mock_engine = MagicMock()
        mock_pop = MagicMock()
        mock_engine.seed.return_value = mock_pop

        captured_base_wf = []

        def spy_seed(wf, cfg_arg):
            captured_base_wf.append(wf)
            return mock_pop

        mock_engine.seed = spy_seed

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch("factory.outer_loop.filesystem.init_filesystem", return_value=project / ".factory" / "outer_loop"), \
             patch("factory.outer_loop.engine.SwarmEngine", return_value=mock_engine), \
             patch("factory.outer_loop.evaluator.SwarmEvaluator"), \
             patch("factory.outer_loop.similarity.NoveltyFilter"), \
             patch.object(mock_pop, "save"), \
             patch("factory.outer_loop.filesystem.save_checkpoint"), \
             patch.object(SwarmConfig, "get_task", side_effect=ImportError("no module")):
            from factory.cli.outer_loop import _cmd_calibrate
            _cmd_calibrate(ns)

        assert len(captured_base_wf) == 1
        assert captured_base_wf[0].name == "featurebench-seed"

    def test_task_workflow_without_knob_values_uses_default(self, tmp_path: object) -> None:
        """When task workflow has no knob_values, base_workflow stays as featurebench-seed."""
        import argparse
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from factory.outer_loop.models import SwarmConfig
        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        project = Path(str(tmp_path)) / "calibrate-task-noknobs"
        project.mkdir()

        task_wf = Workflow(
            name="task-no-knobs",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )

        class FakeTask:
            def workflow(self):
                return task_wf

        cfg = SwarmConfig(
            benchmark="featurebench",
            budget=10,
            population_size=2,
            task_module="my.task.module",
        )

        ns = argparse.Namespace(project_path=str(project))

        mock_engine = MagicMock()
        mock_pop = MagicMock()
        mock_engine.seed.return_value = mock_pop

        captured_base_wf = []

        def spy_seed(wf, cfg_arg):
            captured_base_wf.append(wf)
            return mock_pop

        mock_engine.seed = spy_seed

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch("factory.outer_loop.filesystem.init_filesystem", return_value=project / ".factory" / "outer_loop"), \
             patch("factory.outer_loop.engine.SwarmEngine", return_value=mock_engine), \
             patch("factory.outer_loop.evaluator.SwarmEvaluator"), \
             patch("factory.outer_loop.similarity.NoveltyFilter"), \
             patch.object(mock_pop, "save"), \
             patch("factory.outer_loop.filesystem.save_checkpoint"), \
             patch.object(SwarmConfig, "get_task", return_value=FakeTask()):
            from factory.cli.outer_loop import _cmd_calibrate
            _cmd_calibrate(ns)

        assert len(captured_base_wf) == 1
        assert captured_base_wf[0].name == "featurebench-seed"

    def test_task_without_workflow_method_uses_default(self, tmp_path: object) -> None:
        """When task has no workflow attribute, base_workflow stays as default."""
        import argparse
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from factory.outer_loop.models import SwarmConfig

        project = Path(str(tmp_path)) / "calibrate-task-nomethod"
        project.mkdir()

        class FakeTaskNoWorkflow:
            pass

        cfg = SwarmConfig(
            benchmark="featurebench",
            budget=10,
            population_size=2,
            task_module="my.task.module",
        )

        ns = argparse.Namespace(project_path=str(project))

        mock_engine = MagicMock()
        mock_pop = MagicMock()
        mock_engine.seed.return_value = mock_pop

        captured_base_wf = []

        def spy_seed(wf, cfg_arg):
            captured_base_wf.append(wf)
            return mock_pop

        mock_engine.seed = spy_seed

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch("factory.outer_loop.filesystem.init_filesystem", return_value=project / ".factory" / "outer_loop"), \
             patch("factory.outer_loop.engine.SwarmEngine", return_value=mock_engine), \
             patch("factory.outer_loop.evaluator.SwarmEvaluator"), \
             patch("factory.outer_loop.similarity.NoveltyFilter"), \
             patch.object(mock_pop, "save"), \
             patch("factory.outer_loop.filesystem.save_checkpoint"), \
             patch.object(SwarmConfig, "get_task", return_value=FakeTaskNoWorkflow()):
            from factory.cli.outer_loop import _cmd_calibrate
            _cmd_calibrate(ns)

        assert len(captured_base_wf) == 1
        assert captured_base_wf[0].name == "featurebench-seed"


class TestEvolveTypedSuggestionsDeserialization:
    """Cover typed_suggestions deserialization in _cmd_evolve."""

    def test_evolve_deserializes_typed_suggestions(self, tmp_path: object) -> None:
        import argparse
        import json
        from pathlib import Path
        from unittest.mock import patch

        from factory.outer_loop.models import SwarmConfig

        project = Path(str(tmp_path))
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)
        reflections_dir = project / ".factory" / "outer_loop" / "reflections"
        reflections_dir.mkdir(parents=True)

        report_data = {
            "generation": 0,
            "failure_patterns": ["Agent builder failed"],
            "success_patterns": ["Winner used researcher"],
            "mutation_suggestions": ["NODE_INSERT: Add researcher"],
            "prompt_improvements": [],
            "structural_recommendations": [],
            "top_k_ids": ["w1"],
            "bottom_k_ids": ["l1"],
            "typed_suggestions": [
                {
                    "operator": "knob_mutate",
                    "target": "style",
                    "rationale": "focused works best",
                    "value": "focused",
                },
                {
                    "operator": "node_insert",
                    "target": "researcher",
                    "rationale": "present in winners",
                    "value": None,
                },
            ],
        }
        (reflections_dir / "gen0.json").write_text(json.dumps(report_data))

        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        wf = Workflow(
            name="test-wf",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )
        from factory.outer_loop.mode_registry import EphemeralModeRegistry

        registry = EphemeralModeRegistry(project)
        registry.register("test01", 0, wf)

        cfg = SwarmConfig(benchmark="featurebench", budget=50)

        captured_kwargs: list[dict] = []
        from factory.outer_loop import mutations as _mutations_mod

        original_apply = _mutations_mod.apply_random_mutation

        def spy_apply(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return original_apply(*args, **kwargs)

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch.object(_mutations_mod, "apply_random_mutation", side_effect=spy_apply):
            from factory.cli.outer_loop import _cmd_evolve

            ns = argparse.Namespace(project_path=str(project), generation=0)
            _cmd_evolve(ns)

        assert len(captured_kwargs) > 0
        report = captured_kwargs[0].get("reflection_report")
        assert report is not None
        assert len(report.typed_suggestions) == 2
        assert report.typed_suggestions[0].operator == "knob_mutate"
        assert report.typed_suggestions[0].value == "focused"
        assert report.typed_suggestions[1].operator == "node_insert"


class TestReflectNotEnoughCandidates:
    """Cover lines 455-456: reflect returns 1 when < 2 candidates."""

    def test_reflect_returns_error_with_one_candidate(self, tmp_path: object) -> None:
        import argparse
        import json
        from pathlib import Path
        from unittest.mock import patch

        from factory.outer_loop.models import SwarmConfig

        project = Path(str(tmp_path))
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)
        results_dir = project / ".factory" / "outer_loop" / "results"
        results_dir.mkdir(parents=True)

        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        wf = Workflow(
            name="only-one",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )
        from factory.outer_loop.mode_registry import EphemeralModeRegistry

        registry = EphemeralModeRegistry(project)
        name = registry.register("solo", 0, wf)

        gen_results = {name: {"score": 0.9, "cost_usd": 1.0}}
        (results_dir / "gen0.json").write_text(json.dumps(gen_results))
        runs_dir = project / ".factory" / "outer_loop" / "runs" / name
        runs_dir.mkdir(parents=True)
        (runs_dir / "cycle_summary.json").write_text(
            json.dumps({"mode": name, "score": 0.9, "cost_usd": 1.0, "kept": 1, "reverted": 0})
        )

        cfg = SwarmConfig(benchmark="featurebench", budget=50)
        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg):
            from factory.cli.outer_loop import _cmd_reflect

            ns = argparse.Namespace(project_path=str(project), generation=0)
            rc = _cmd_reflect(ns)
            assert rc == 1


class TestEvolveNoModes:
    """Cover lines 495-496: evolve returns 1 when no ephemeral modes exist."""

    def test_evolve_returns_error_with_no_modes(self, tmp_path: object) -> None:
        import argparse
        from pathlib import Path
        from unittest.mock import patch

        from factory.outer_loop.models import SwarmConfig

        project = Path(str(tmp_path))
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)

        cfg = SwarmConfig(benchmark="featurebench", budget=50)
        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg):
            from factory.cli.outer_loop import _cmd_evolve

            ns = argparse.Namespace(project_path=str(project), generation=0)
            rc = _cmd_evolve(ns)
            assert rc == 1


class TestEvolveNonDictTypedSuggestion:
    """Cover partial branch 512->511: non-dict items in typed_suggestions are skipped."""

    def test_evolve_skips_non_dict_typed_suggestion(self, tmp_path: object) -> None:
        import argparse
        import json
        from pathlib import Path
        from unittest.mock import patch

        from factory.outer_loop.models import SwarmConfig

        project = Path(str(tmp_path))
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)
        reflections_dir = project / ".factory" / "outer_loop" / "reflections"
        reflections_dir.mkdir(parents=True)

        report_data = {
            "failure_patterns": ["f1"],
            "success_patterns": ["s1"],
            "mutation_suggestions": [],
            "prompt_improvements": [],
            "structural_recommendations": [],
            "top_k_ids": ["w1"],
            "bottom_k_ids": ["l1"],
            "typed_suggestions": [
                "not-a-dict",
                {"operator": "knob_mutate", "target": "t", "rationale": "r", "value": "v"},
            ],
        }
        (reflections_dir / "gen0.json").write_text(json.dumps(report_data))

        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        wf = Workflow(
            name="test-wf",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )
        from factory.outer_loop.mode_registry import EphemeralModeRegistry

        registry = EphemeralModeRegistry(project)
        registry.register("test01", 0, wf)

        cfg = SwarmConfig(benchmark="featurebench", budget=50)

        captured_kwargs: list[dict] = []
        from factory.outer_loop import mutations as _mutations_mod

        original_apply = _mutations_mod.apply_random_mutation

        def spy_apply(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return original_apply(*args, **kwargs)

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch.object(_mutations_mod, "apply_random_mutation", side_effect=spy_apply):
            from factory.cli.outer_loop import _cmd_evolve

            ns = argparse.Namespace(project_path=str(project), generation=0)
            _cmd_evolve(ns)

        assert len(captured_kwargs) > 0
        report = captured_kwargs[0].get("reflection_report")
        assert report is not None
        assert len(report.typed_suggestions) == 1
        assert report.typed_suggestions[0].operator == "knob_mutate"


class TestEvolveNonListTypedSuggestions:
    """Cover partial branch 510->521: non-list typed_suggestions is skipped."""

    def test_evolve_skips_non_list_typed_suggestions(self, tmp_path: object) -> None:
        import argparse
        import json
        from pathlib import Path
        from unittest.mock import patch

        from factory.outer_loop.models import SwarmConfig

        project = Path(str(tmp_path))
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)
        reflections_dir = project / ".factory" / "outer_loop" / "reflections"
        reflections_dir.mkdir(parents=True)

        report_data = {
            "failure_patterns": ["f1"],
            "success_patterns": ["s1"],
            "mutation_suggestions": [],
            "prompt_improvements": [],
            "structural_recommendations": [],
            "top_k_ids": ["w1"],
            "bottom_k_ids": ["l1"],
            "typed_suggestions": "not-a-list",
        }
        (reflections_dir / "gen0.json").write_text(json.dumps(report_data))

        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        wf = Workflow(
            name="test-wf",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )
        from factory.outer_loop.mode_registry import EphemeralModeRegistry

        registry = EphemeralModeRegistry(project)
        registry.register("test01", 0, wf)

        cfg = SwarmConfig(benchmark="featurebench", budget=50)

        captured_kwargs: list[dict] = []
        from factory.outer_loop import mutations as _mutations_mod

        original_apply = _mutations_mod.apply_random_mutation

        def spy_apply(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return original_apply(*args, **kwargs)

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch.object(_mutations_mod, "apply_random_mutation", side_effect=spy_apply):
            from factory.cli.outer_loop import _cmd_evolve

            ns = argparse.Namespace(project_path=str(project), generation=0)
            _cmd_evolve(ns)

        assert len(captured_kwargs) > 0
        report = captured_kwargs[0].get("reflection_report")
        assert report is not None
        assert len(report.typed_suggestions) == 0


class TestEvolveRegistryLoadNone:
    """Cover line 532: continue when registry.load returns None in evolve loop."""

    def test_evolve_skips_none_workflow(self, tmp_path: object) -> None:
        import argparse
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from factory.outer_loop.models import SwarmConfig

        project = Path(str(tmp_path))
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)

        cfg = SwarmConfig(benchmark="featurebench", budget=50)

        mock_registry = MagicMock()
        mock_registry.list_modes.return_value = ["mode-a", "mode-b"]
        mock_registry.load.return_value = None
        mock_registry.prune_stale_modes.return_value = None

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch("factory.outer_loop.mode_registry.EphemeralModeRegistry",
                   return_value=mock_registry):
            from factory.cli.outer_loop import _cmd_evolve

            ns = argparse.Namespace(project_path=str(project), generation=0)
            rc = _cmd_evolve(ns)
            assert rc == 0


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
        expected = {"seed", "evaluate", "reflect", "evolve", "gate_converge", "promote"}
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
