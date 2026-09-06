"""End-to-end acceptance tests for the Task abstraction.

Validates all four acceptance criteria from the design doc:
1. TaskDefinition.from_toml('chess-evolve.toml') produces a valid task
2. compose(compatible_workflow, task, project_dir) returns InnerLoop
3. The task's scoring contract drives evaluator selection
4. Same workflow composes with different tasks (swap chess-evolve for featurebench)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.compose import (
    TaskProtocol,
    compose,
    validate_composition,
)
from factory.task import (
    Task,
    TaskDefinition,
    TaskInstance,
    VerifyResult,
)


_CHESS_EVOLVE_TOML = Path(__file__).parent.parent / "benchmarks" / "configs" / "chess-evolve.toml"


def _make_builder_workflow(name: str = "improve"):
    """Create a workflow with builder capabilities."""
    from factory.workflow.primitives import AgentNode, AgentRole, Workflow

    return Workflow(
        name=name,
        nodes={
            "builder": AgentNode(
                id="builder",
                role=AgentRole.BUILDER,
                prompt_template="build it",
            ),
        },
        edges=[],
        start_node="builder",
    )


# ── AC-1: Task Creation ────────────────────────────────────────


class TestChessEvolveE2E:
    """Validates all four acceptance criteria using chess-evolve."""

    # AC-1.1: from_toml produces valid TaskDefinition
    def test_from_toml_produces_valid_definition(self):
        defn = TaskDefinition.from_toml(_CHESS_EVOLVE_TOML)
        assert defn.name == "chess-evolve"
        assert defn.description == "Evolve a chess engine that beats the baseline"

    # AC-1.2: scoring maps to exit_code (legacy 'pytest' method)
    def test_scoring_is_exit_code(self):
        defn = TaskDefinition.from_toml(_CHESS_EVOLVE_TOML)
        assert defn.scoring.method == "exit_code"

    # AC-1.3: instances() yields at least one TaskInstance
    def test_instances_yields_instance(self):
        task = Task.from_toml(_CHESS_EVOLVE_TOML)
        insts = list(task.instances())
        assert len(insts) >= 1
        assert insts[0].id  # non-empty id

    # AC-1.4: setup() completes without error
    def test_setup_completes(self, tmp_path: Path):
        task = Task.from_toml(_CHESS_EVOLVE_TOML)
        inst = TaskInstance(id="test-instance")
        # setup runs pip install, which will fail for a non-existent dir,
        # but should not raise (returns _RunResult)
        task.setup(inst, tmp_path)

    # AC-1.5: prompt() returns non-empty string
    def test_prompt_returns_string(self):
        task = Task.from_toml(_CHESS_EVOLVE_TOML)
        inst = TaskInstance(id="test")
        p = task.prompt(inst)
        assert isinstance(p, str)
        assert len(p) > 0
        assert "chess" in p.lower() or "engine" in p.lower()

    # AC-1.6: verify() returns VerifyResult
    def test_verify_returns_verify_result(self, tmp_path: Path):
        task = Task.from_toml(_CHESS_EVOLVE_TOML)
        inst = TaskInstance(id="test")
        result = task.verify(inst, tmp_path)
        assert isinstance(result, VerifyResult)
        assert isinstance(result.passed, bool)
        assert isinstance(result.score, float)

    # AC-1.7: constraints.timeout >= 60
    def test_constraints_timeout(self):
        defn = TaskDefinition.from_toml(_CHESS_EVOLVE_TOML)
        assert defn.constraints.timeout >= 60
        assert defn.constraints.timeout == 3600

    # AC-1.8: serialization roundtrip
    def test_serialization_roundtrip(self):
        defn = TaskDefinition.from_toml(_CHESS_EVOLVE_TOML)
        data = defn.model_dump(mode="json")
        restored = TaskDefinition.model_validate(data)
        assert restored.name == defn.name
        assert restored.scoring.method == defn.scoring.method

    # AC-1.9: isinstance(task, Task) and Protocol
    def test_isinstance_check(self):
        task = Task.from_toml(_CHESS_EVOLVE_TOML)
        assert isinstance(task, Task)
        assert isinstance(task, TaskProtocol)

    # AC-1.10: TOML-only task has working default hooks
    def test_toml_only_default_hooks(self):
        task = Task.from_toml(_CHESS_EVOLVE_TOML)
        # instances() works
        insts = list(task.instances())
        assert len(insts) >= 1
        # prompt() works
        p = task.prompt(insts[0])
        assert len(p) > 0

    # AC-2.5: compose returns InnerLoop
    def test_compose_returns_inner_loop(self, tmp_path: Path):
        wf = _make_builder_workflow()
        task = Task.from_toml(_CHESS_EVOLVE_TOML)
        loop = compose(wf, task, tmp_path)
        from factory.inner_loop import InnerLoop

        assert isinstance(loop, InnerLoop)

    # AC-2.6: validate_composition succeeds
    def test_validate_composition_succeeds(self):
        wf = _make_builder_workflow()
        task = Task.from_toml(_CHESS_EVOLVE_TOML)
        validate_composition(wf, task)  # should not raise

    # AC-3.2: get_evaluator returns correct type
    def test_get_evaluator(self):
        task = Task.from_toml(_CHESS_EVOLVE_TOML)
        evaluator = task.get_evaluator()
        from factory.outer_loop.evaluators.exit_code import ExitCodeEvaluator

        assert isinstance(evaluator, ExitCodeEvaluator)

    # AC-3.3: prompt comes from task
    def test_prompt_from_task(self):
        task = Task.from_toml(_CHESS_EVOLVE_TOML)
        inst = TaskInstance(id="test")
        p = task.prompt(inst)
        assert "chess" in p.lower() or "engine" in p.lower()

    # AC-4.1: Same workflow composes with different tasks
    def test_same_workflow_different_tasks(self, tmp_path: Path):
        wf = _make_builder_workflow()

        # Chess-evolve task
        chess_task = Task.from_toml(_CHESS_EVOLVE_TOML)
        chess_loop = compose(wf, chess_task, tmp_path)

        # Featurebench task (from legacy)
        fb_task = Task.from_legacy(
            name="featurebench",
            test_command="pytest -xvs",
            test_format="pytest",
        )
        fb_loop = compose(wf, fb_task, tmp_path)

        from factory.inner_loop import InnerLoop

        assert isinstance(chess_loop, InnerLoop)
        assert isinstance(fb_loop, InnerLoop)

    # AC-4.2: BenchmarkConfig.to_task() for featurebench
    def test_benchmark_config_to_task_featurebench(self):
        from factory.outer_loop.benchmark_config import BenchmarkConfig

        bc = BenchmarkConfig(
            name="featurebench",
            test_format="pytest",
            test_command="pytest -xvs",
        )
        task = bc.to_task()
        assert isinstance(task, Task)
        assert task.scoring.method == "exit_code"

    # AC-4.3: BenchmarkConfig.to_task() for swebench
    def test_benchmark_config_to_task_swebench(self):
        from factory.outer_loop.benchmark_config import BenchmarkConfig

        bc = BenchmarkConfig(
            name="swebench",
            test_format="exit_code",
            test_command="pytest -xvs",
        )
        task = bc.to_task()
        assert task.scoring.method == "exit_code"

    # AC-4.4: BenchmarkConfig.to_task() for AIME (exact_match maps to exit_code)
    def test_benchmark_config_to_task_aime(self):
        from factory.outer_loop.benchmark_config import BenchmarkConfig

        bc = BenchmarkConfig(
            name="aime",
            test_format="exact_match",
        )
        task = bc.to_task()
        assert task.scoring.method == "exit_code"

    # AC-4.5: ResearchTarget.to_task()
    def test_research_target_to_task(self):
        from factory.models import ResearchTarget

        rt = ResearchTarget(
            objective="Optimize speed",
            metric="speed_ms",
            target=100.0,
            run_command="python bench.py",
            result_path="metrics.speed",
        )
        task = rt.to_task()
        assert task.scoring.method == "json"


# ── TaskRegistry tests ──────────────────────────────────────────


class TestTaskRegistry:
    def test_discover_builtin_tasks(self):
        from factory.task_registry import TaskRegistry

        TaskRegistry.reset()
        entries = TaskRegistry.discover()
        names = set(entries.keys())
        # chess-evolve uses [task] format — always discoverable
        assert "chess-evolve" in names

    def test_load_chess_evolve(self):
        from factory.task_registry import TaskRegistry

        TaskRegistry.reset()
        task = TaskRegistry.load_task("chess-evolve")
        assert task.name == "chess-evolve"
        assert task.scoring.method == "exit_code"

    def test_load_nonexistent_raises(self):
        from factory.task_registry import TaskRegistry

        TaskRegistry.reset()
        TaskRegistry.discover()
        with pytest.raises(KeyError, match="nonexistent"):
            TaskRegistry.load_task("nonexistent")

    def test_list_tasks(self):
        from factory.task_registry import TaskRegistry

        TaskRegistry.reset()
        entries = TaskRegistry.list_tasks()
        assert len(entries) >= 2  # at least chess-evolve and featurebench
        names = [e.name for e in entries]
        assert "chess-evolve" in names

    def test_project_local_shadows_builtin(self, tmp_path: Path):
        from factory.task_registry import TaskRegistry

        # Create a project-local task that shadows featurebench
        task_dir = tmp_path / ".factory" / "tasks"
        task_dir.mkdir(parents=True)
        (task_dir / "featurebench.toml").write_text("""
[task]
name = "featurebench"
description = "Project-local override"

[scoring]
method = "exit_code"
""")
        TaskRegistry.reset()
        entries = TaskRegistry.discover(tmp_path)
        assert entries["featurebench"].source == "project"
        assert entries["featurebench"].description == "Project-local override"

    def test_py_file_discovery(self, tmp_path: Path):
        """Task .py files with meta + task() function are discovered."""
        from factory.task_registry import TaskRegistry

        task_dir = tmp_path / ".factory" / "tasks"
        task_dir.mkdir(parents=True)
        (task_dir / "custom.py").write_text("""
from factory.task import Task, TaskDefinition

meta = {"name": "custom-task", "description": "A custom task"}

def task():
    return Task(definition=TaskDefinition(name="custom-task", description="A custom task"))
""")
        TaskRegistry.reset()
        entries = TaskRegistry.discover(tmp_path)
        assert "custom-task" in entries
        assert entries["custom-task"].source == "project"

        task = TaskRegistry.load_task("custom-task", tmp_path)
        assert task.name == "custom-task"
