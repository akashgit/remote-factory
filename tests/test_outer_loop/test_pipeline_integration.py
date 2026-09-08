"""CLI pipeline integration tests for outer-loop commands.

Chains real main(["outer-loop", ...]) calls against a shared tmp_path project
directory, asserting both exit codes and on-disk .factory/outer_loop/ artifacts
between each step. This catches cross-stage serialization drift — the exact bug
class that b90edfca fixed.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
from collections import namedtuple
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from factory.cli import main
from factory.outer_loop.filesystem import load_checkpoint, load_config
from factory.outer_loop.models import EvalResult
from factory.outer_loop.population import Population
from factory.workflow.primitives import AgentNode, AgentRole, Workflow

_SEED_WORKFLOW = Workflow(
    name="test-seed",
    nodes={
        "builder": AgentNode(
            id="builder",
            role=AgentRole.BUILDER,
            model="opus",
            timeout=7200,
        ),
    },
    edges=[],
    start_node="builder",
    terminal=True,
)


def _outer_loop_project(tmp_path: Path) -> Path:
    """Create a minimal project with git init and .factory/ structure."""
    project = tmp_path / "ol-project"
    project.mkdir()
    subprocess.run(
        ["git", "init"],
        cwd=project,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=project,
        capture_output=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@test.com",
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
    )
    (project / ".factory").mkdir()
    return project


DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])
_AMPLE_DISK = DiskUsage(total=500 * 1024**3, used=100 * 1024**3, free=400 * 1024**3)

_MOCK_EVAL_RESULT = EvalResult(
    score=0.75,
    benchmark_score=0.75,
    cost_usd=0.01,
)


def _run_calibrate(project: Path) -> int:
    """Run the calibrate step with standard mocks."""
    with (
        patch("factory.cli.outer_loop.shutil.disk_usage", return_value=_AMPLE_DISK),
        patch("factory.agents.runner.invoke_agent", side_effect=RuntimeError("should not call agent")),
        patch("factory.cli.outer_loop._resolve_seed_workflow", return_value=_SEED_WORKFLOW),
    ):
        return main([
            "outer-loop", "calibrate", str(project),
            "--benchmark", "featurebench",
            "--budget", "20",
            "--population-size", "2",
            "--seed-workflow", "test.module:build_pipeline",
        ])


def _run_evaluate(project: Path, generation: int = 0) -> int:
    """Run the evaluate step with a mocked evaluator."""
    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.return_value = _MOCK_EVAL_RESULT

    with (
        patch("factory.outer_loop.evaluator.SwarmEvaluator", return_value=mock_evaluator),
        patch("factory.agents.runner.invoke_agent", side_effect=RuntimeError("should not call agent")),
    ):
        return main([
            "outer-loop", "evaluate", str(project),
            "--generation", str(generation),
        ])


def _run_evaluate_varied(project: Path, generation: int = 0) -> int:
    """Run evaluate with scores that differ per mode (for contrastive reflection)."""
    _call_count = {"n": 0}
    _scores = [0.9, 0.3, 0.6, 0.1]

    def _varied_evaluate(*_args: object, **_kwargs: object) -> EvalResult:
        idx = _call_count["n"] % len(_scores)
        _call_count["n"] += 1
        return EvalResult(score=_scores[idx], benchmark_score=_scores[idx], cost_usd=0.01)

    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.side_effect = _varied_evaluate

    with (
        patch("factory.outer_loop.evaluator.SwarmEvaluator", return_value=mock_evaluator),
        patch("factory.agents.runner.invoke_agent", side_effect=RuntimeError("should not call agent")),
    ):
        return main([
            "outer-loop", "evaluate", str(project),
            "--generation", str(generation),
        ])


def _run_reflect(
    project: Path, generation: int = 0, *, mock_llm_reflect: bool = True,
) -> int:
    """Run the reflect step — real reflector, mocked agent guard only.

    By default mocks ``_llm_reflect`` so the real ``claude -p`` subprocess is
    never spawned (PR #1472 enables ``llm_reflect=True`` in the CLI).  Pass
    ``mock_llm_reflect=False`` when a test needs to exercise ``_llm_reflect``
    itself (with the subprocess mocked at a lower level).
    """
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch("factory.agents.runner.invoke_agent", side_effect=RuntimeError("should not call agent")),
        )
        if mock_llm_reflect:
            stack.enter_context(
                patch("factory.outer_loop.reflector.OuterLoopReflector._llm_reflect"),
            )
        return main([
            "outer-loop", "reflect", str(project),
            "--generation", str(generation),
        ])


def _run_evolve(project: Path, generation: int = 0) -> int:
    """Run the evolve step with standard mocks."""
    with (
        patch("factory.cli.outer_loop.shutil.disk_usage", return_value=_AMPLE_DISK),
        patch("factory.agents.runner.invoke_agent", side_effect=RuntimeError("should not call agent")),
    ):
        return main([
            "outer-loop", "evolve", str(project),
            "--generation", str(generation),
        ])


def _run_calibrate_with_project_dir(project: Path, target: Path) -> int:
    """Run calibrate with --project-dir to mirror modes to a target directory."""
    with (
        patch("factory.cli.outer_loop.shutil.disk_usage", return_value=_AMPLE_DISK),
        patch("factory.agents.runner.invoke_agent", side_effect=RuntimeError("should not call agent")),
        patch("factory.cli.outer_loop._resolve_seed_workflow", return_value=_SEED_WORKFLOW),
    ):
        return main([
            "outer-loop", "calibrate", str(project),
            "--benchmark", "featurebench",
            "--budget", "20",
            "--population-size", "2",
            "--project-dir", str(target),
            "--seed-workflow", "test.module:build_pipeline",
        ])


def _run_evaluate_with_project_dir(
    project: Path, target: Path, generation: int = 0,
) -> int:
    """Run evaluate with --project-dir pointing to a separate target."""
    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.return_value = _MOCK_EVAL_RESULT

    with (
        patch("factory.outer_loop.evaluator.SwarmEvaluator", return_value=mock_evaluator),
        patch("factory.agents.runner.invoke_agent", side_effect=RuntimeError("should not call agent")),
    ):
        return main([
            "outer-loop", "evaluate", str(project),
            "--generation", str(generation),
            "--project-dir", str(target),
        ])


def _run_promote(project: Path, mode_name: str, permanent_name: str) -> int:
    """Run the promote step."""
    return main([
        "outer-loop", "promote", str(project),
        "--mode-name", mode_name,
        "--permanent-name", permanent_name,
    ])


def _run_status(project: Path) -> int:
    """Run the status step."""
    return main(["outer-loop", "status", str(project)])


class TestOuterLoopCLIPipeline:
    def test_calibrate_creates_artifacts(self, tmp_path: Path) -> None:
        project = _outer_loop_project(tmp_path)
        rc = _run_calibrate(project)
        assert rc == 0

        ol_root = project / ".factory" / "outer_loop"

        config = load_config(project)
        assert config is not None
        assert config.benchmark == "featurebench"
        assert config.budget == 20
        assert config.population_size == 2

        state = load_checkpoint(project)
        assert state is not None
        assert state.generation == 0

        pop_dir = ol_root / "population"
        assert (pop_dir / "population.json").exists()
        pop = Population.load(pop_dir)
        assert pop.size == 2

        modes_dir = ol_root / "modes"
        assert modes_dir.exists()
        mode_files = list(modes_dir.glob("*.json"))
        assert len(mode_files) >= 2

    def test_calibrate_then_evaluate(self, tmp_path: Path) -> None:
        project = _outer_loop_project(tmp_path)

        rc_cal = _run_calibrate(project)
        assert rc_cal == 0

        rc_eval = _run_evaluate(project, generation=0)
        assert rc_eval == 0

        ol_root = project / ".factory" / "outer_loop"
        results_path = ol_root / "results" / "gen0.json"
        assert results_path.exists()
        results = json.loads(results_path.read_text())
        assert len(results) > 0
        for mode_name, scores in results.items():
            assert "score" in scores
            assert scores["score"] == 0.75

        state = load_checkpoint(project)
        assert state is not None
        assert state.total_evaluations > 0
        assert state.best_score > 0

    def test_evaluate_then_reflect(self, tmp_path: Path) -> None:
        project = _outer_loop_project(tmp_path)

        assert _run_calibrate(project) == 0
        assert _run_evaluate(project, generation=0) == 0
        rc_reflect = _run_reflect(project, generation=0)
        assert rc_reflect == 0

        ol_root = project / ".factory" / "outer_loop"
        reflections_dir = ol_root / "reflections"
        gen0_json = reflections_dir / "gen0.json"
        gen0_md = reflections_dir / "gen0.md"
        assert gen0_json.exists() or gen0_md.exists(), (
            "reflect should produce gen0.json or gen0.md"
        )

        if gen0_json.exists():
            report_data = json.loads(gen0_json.read_text())
            assert "failure_patterns" in report_data or "success_patterns" in report_data

    def test_reflect_then_evolve(self, tmp_path: Path) -> None:
        project = _outer_loop_project(tmp_path)

        assert _run_calibrate(project) == 0
        assert _run_evaluate(project, generation=0) == 0
        assert _run_reflect(project, generation=0) == 0

        rc_evolve = _run_evolve(project, generation=0)
        assert rc_evolve == 0

        ol_root = project / ".factory" / "outer_loop"
        modes_dir = ol_root / "modes"
        assert modes_dir.exists()
        mode_files = list(modes_dir.glob("*.json"))
        gen1_modes = [f for f in mode_files if "gen1" in f.name]
        assert len(gen1_modes) > 0, "evolve should create gen-1 mode files"

    def test_full_pipeline_calibrate_through_status(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        project = _outer_loop_project(tmp_path)

        assert _run_calibrate(project) == 0
        assert _run_evaluate(project, generation=0) == 0
        assert _run_reflect(project, generation=0) == 0
        assert _run_evolve(project, generation=0) == 0

        rc_status = _run_status(project)
        assert rc_status == 0

        out = capsys.readouterr().out

        assert "Generation:" in out
        assert "Best score:" in out
        assert "Ephemeral modes:" in out

        ol_root = project / ".factory" / "outer_loop"
        modes_dir = ol_root / "modes"
        mode_files = list(modes_dir.glob("*.json"))
        gen0_modes = [f for f in mode_files if "gen0" in f.name]
        gen1_modes = [f for f in mode_files if "gen1" in f.name]
        assert len(gen0_modes) > 0, "should have gen-0 modes from calibrate"
        assert len(gen1_modes) > 0, "should have gen-1 modes from evolve"

        config = load_config(project)
        assert config is not None
        state = load_checkpoint(project)
        assert state is not None
        assert state.total_evaluations > 0

        pop = Population.load(ol_root / "population")
        assert pop.size > 0

    def test_evaluate_with_project_dir(self, tmp_path: Path) -> None:
        """--project-dir mirrors mode files to a separate target directory."""
        ol_project = _outer_loop_project(tmp_path)
        eval_target = tmp_path / "eval-target"
        eval_target.mkdir()
        subprocess.run(
            ["git", "init"],
            cwd=eval_target,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "initial"],
            cwd=eval_target,
            capture_output=True,
            check=True,
            env={
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "test@test.com",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "test@test.com",
                "HOME": str(tmp_path),
                "PATH": "/usr/bin:/bin:/usr/local/bin",
            },
        )
        (eval_target / ".factory").mkdir()

        assert _run_calibrate_with_project_dir(ol_project, eval_target) == 0

        target_modes = eval_target / ".factory" / "outer_loop" / "modes"
        assert target_modes.exists(), "calibrate should mirror mode files to target dir"
        target_mode_files = list(target_modes.glob("*.json"))
        assert len(target_mode_files) > 0, "target dir should contain mirrored mode JSONs"

        rc_eval = _run_evaluate_with_project_dir(ol_project, eval_target, generation=0)
        assert rc_eval == 0

        results_path = ol_project / ".factory" / "outer_loop" / "results" / "gen0.json"
        assert results_path.exists()
        results = json.loads(results_path.read_text())
        assert len(results) > 0

        source_modes = ol_project / ".factory" / "outer_loop" / "modes"
        source_names = {f.name for f in source_modes.glob("evolve-gen0-*.json")}
        target_names = {f.name for f in target_mode_files}
        assert source_names & target_names, "target should mirror source mode files"

    def test_promote_reads_calibrate_modes(self, tmp_path: Path) -> None:
        """promote can read mode files written by calibrate."""
        project = _outer_loop_project(tmp_path)
        assert _run_calibrate(project) == 0

        modes_dir = project / ".factory" / "outer_loop" / "modes"
        mode_files = sorted(modes_dir.glob("evolve-gen0-*.json"))
        assert len(mode_files) > 0, "calibrate should create mode files"
        mode_name = mode_files[0].stem

        rc = _run_promote(project, mode_name, "promoted-test")
        assert rc == 0

        dest = project / "factory" / "workflow" / "contributed" / "promoted-test" / "workflow.json"
        assert dest.exists(), "promoted workflow should be written to contributed/"
        data = json.loads(dest.read_text())
        assert data["name"] == "promoted-test"
        assert "_content_hash" not in data

    def test_reflect_loads_cycle_summary(self, tmp_path: Path) -> None:
        """reflect deserializes cycle_summary.json written by evaluate."""
        project = _outer_loop_project(tmp_path)

        assert _run_calibrate(project) == 0
        assert _run_evaluate(project, generation=0) == 0

        runs_dir = project / ".factory" / "outer_loop" / "runs"
        assert runs_dir.exists(), "evaluate should create runs/ directory"
        summaries = list(runs_dir.glob("*/cycle_summary.json"))
        assert len(summaries) > 0, "evaluate should write cycle_summary.json per mode"

        rc_reflect = _run_reflect(project, generation=0)
        assert rc_reflect == 0

        gen0_json = project / ".factory" / "outer_loop" / "reflections" / "gen0.json"
        assert gen0_json.exists(), "reflect should produce gen0.json"
        report = json.loads(gen0_json.read_text())

        expected_modes = len(summaries)
        top_k = report.get("top_k_ids", [])
        bottom_k = report.get("bottom_k_ids", [])
        reflected_count = len(set(top_k) | set(bottom_k))
        assert reflected_count == expected_modes, (
            f"reflection should cover all {expected_modes} modes with cycle summaries, "
            f"got {reflected_count}"
        )

    def test_calibrate_knob_propagation(self, tmp_path: Path) -> None:
        """Seed workflow knob_values/bounds/expandable survive into population."""
        knob_workflow = Workflow(
            name="knob-seed",
            nodes={
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    model="opus",
                    timeout=7200,
                ),
            },
            edges=[],
            start_node="builder",
            terminal=True,
            knob_values={"temperature": 0.5},
            knob_bounds={"temperature": [0.1, 0.5, 1.0]},
            knob_expandable={"temperature": "Sampling temperature for agent LLM calls"},
        )

        project = _outer_loop_project(tmp_path)

        with (
            patch("factory.cli.outer_loop.shutil.disk_usage", return_value=_AMPLE_DISK),
            patch("factory.agents.runner.invoke_agent", side_effect=RuntimeError("should not call agent")),
            patch("factory.cli.outer_loop._resolve_seed_workflow", return_value=knob_workflow),
        ):
            rc = main([
                "outer-loop", "calibrate", str(project),
                "--benchmark", "featurebench",
                "--budget", "20",
                "--population-size", "2",
                "--seed-workflow", "test:knob_pipeline",
            ])

        assert rc == 0

        pop_dir = project / ".factory" / "outer_loop" / "population"
        pop = Population.load(pop_dir)
        assert pop.size >= 1

        found_knob = False
        for ind in pop.individuals:
            kv = ind.workflow_data.get("knob_values", {})
            if "temperature" in kv:
                found_knob = True
                break
        assert found_knob, "at least one individual should carry knob_values with 'temperature'"

    def test_typed_suggestions_deserialization(self, tmp_path: Path) -> None:
        """typed_suggestions written by reflect are deserialized by evolve."""
        project = _outer_loop_project(tmp_path)
        assert _run_calibrate(project) == 0

        assert _run_evaluate_varied(project, generation=0) == 0

        rc_reflect = _run_reflect(project, generation=0)
        assert rc_reflect == 0

        gen0_json = project / ".factory" / "outer_loop" / "reflections" / "gen0.json"
        assert gen0_json.exists(), "reflect should produce gen0.json"
        report_data = json.loads(gen0_json.read_text())

        typed = report_data.get("typed_suggestions", [])
        assert isinstance(typed, list), "typed_suggestions should be a list"

        for ts in typed:
            assert "operator" in ts, "each typed suggestion needs an 'operator'"
            assert "rationale" in ts, "each typed suggestion needs a 'rationale'"

        rc_evolve = _run_evolve(project, generation=0)
        assert rc_evolve == 0, "evolve must deserialize typed_suggestions without crashing"
