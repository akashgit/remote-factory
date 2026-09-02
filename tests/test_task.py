"""Tests for factory/task.py — TaskDefinition, ScoringContract, four hooks, backward compat."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.task import (
    ExactMatchScoring,
    ExitCodeScoring,
    InstancesConfig,
    JSONScoring,
    PromptConfig,
    PytestScoring,
    Task,
    TaskConstraints,
    TaskDefinition,
    TaskInstance,
    VerifyResult,
)


# ── TaskInstance tests ───────────────────────────────────────────


class TestTaskInstance:
    def test_create_minimal(self):
        inst = TaskInstance(id="test-01")
        assert inst.id == "test-01"
        assert inst.path is None
        assert inst.metadata == {}

    def test_create_with_path(self, tmp_path: Path):
        inst = TaskInstance(id="t1", path=tmp_path)
        assert inst.path == tmp_path

    def test_create_with_metadata(self):
        inst = TaskInstance(id="t1", metadata={"key": "value"})
        assert inst.metadata["key"] == "value"

    def test_extra_fields_forbidden(self):
        with pytest.raises(Exception):
            TaskInstance(id="t1", unknown="bad")  # type: ignore[call-arg]


# ── VerifyResult tests ───────────────────────────────────────────


class TestVerifyResult:
    def test_create(self):
        vr = VerifyResult(passed=True, score=0.85)
        assert vr.passed is True
        assert vr.score == 0.85
        assert vr.details == {}

    def test_with_details(self):
        vr = VerifyResult(passed=False, score=0.0, details={"error": "timeout"})
        assert vr.details["error"] == "timeout"


# ── ScoringContract tests ───────────────────────────────────────


class TestScoringContract:
    def test_pytest_scoring(self):
        s = PytestScoring()
        assert s.method == "pytest"
        assert s.partial_credit is True

    def test_exit_code_scoring(self):
        s = ExitCodeScoring()
        assert s.method == "exit_code"

    def test_json_scoring(self):
        s = JSONScoring(metric_path="stats.accuracy")
        assert s.method == "json"
        assert s.metric_path == "stats.accuracy"

    def test_exact_match_scoring(self):
        s = ExactMatchScoring(answer_extraction=r"\d+")
        assert s.method == "exact_match"
        assert s.answer_extraction == r"\d+"

    def test_discriminated_union_pytest(self):
        defn = TaskDefinition(
            name="test",
            scoring=PytestScoring(),
        )
        assert isinstance(defn.scoring, PytestScoring)

    def test_discriminated_union_json(self):
        defn = TaskDefinition(
            name="test",
            scoring=JSONScoring(metric_path="result.score"),
        )
        assert isinstance(defn.scoring, JSONScoring)


# ── TaskConstraints tests ───────────────────────────────────────


class TestTaskConstraints:
    def test_defaults(self):
        tc = TaskConstraints()
        assert tc.timeout == 600
        assert tc.max_retries == 1
        assert tc.required_capabilities == []

    def test_custom(self):
        tc = TaskConstraints(timeout=3600, max_retries=3, required_capabilities=["can_run_tests"])
        assert tc.timeout == 3600
        assert tc.max_retries == 3


# ── TaskDefinition tests ────────────────────────────────────────


class TestTaskDefinition:
    def test_create_minimal(self):
        defn = TaskDefinition(name="test-task")
        assert defn.name == "test-task"
        assert isinstance(defn.scoring, PytestScoring)

    def test_from_toml(self, tmp_path: Path):
        toml_content = """
[task]
name = "my-task"
description = "A test task"

[instances]
format = "directory"
source = "data/"

[setup]
command = "pip install -e ."

[prompt]
text = "Do the thing."

[verify]
command = "pytest -xvs"

[scoring]
method = "pytest"
partial_credit = true

[constraints]
timeout = 1800
max_retries = 2
"""
        toml_file = tmp_path / "my-task.toml"
        toml_file.write_text(toml_content)

        defn = TaskDefinition.from_toml(toml_file)
        assert defn.name == "my-task"
        assert defn.description == "A test task"
        assert isinstance(defn.scoring, PytestScoring)
        assert defn.scoring.partial_credit is True
        assert defn.constraints.timeout == 1800
        assert defn.constraints.max_retries == 2
        assert defn.instances_config.format == "directory"
        assert defn.instances_config.source == "data/"
        assert defn.setup_config.command == "pip install -e ."
        assert defn.prompt_config.text == "Do the thing."
        assert defn.verify_config.command == "pytest -xvs"

    def test_from_toml_exit_code(self, tmp_path: Path):
        toml_content = """
[task]
name = "swe-task"

[scoring]
method = "exit_code"
"""
        f = tmp_path / "swe.toml"
        f.write_text(toml_content)
        defn = TaskDefinition.from_toml(f)
        assert isinstance(defn.scoring, ExitCodeScoring)

    def test_from_toml_json_scoring(self, tmp_path: Path):
        toml_content = """
[task]
name = "json-task"

[scoring]
method = "json"
metric_path = "results.accuracy"
"""
        f = tmp_path / "json.toml"
        f.write_text(toml_content)
        defn = TaskDefinition.from_toml(f)
        assert isinstance(defn.scoring, JSONScoring)
        assert defn.scoring.metric_path == "results.accuracy"

    def test_from_toml_exact_match(self, tmp_path: Path):
        toml_content = """
[task]
name = "math-task"

[scoring]
method = "exact_match"
"""
        f = tmp_path / "math.toml"
        f.write_text(toml_content)
        defn = TaskDefinition.from_toml(f)
        assert isinstance(defn.scoring, ExactMatchScoring)

    def test_toml_roundtrip(self):
        """Serialise via model_dump and deserialise via model_validate."""
        defn = TaskDefinition(
            name="roundtrip",
            description="Test roundtrip",
            scoring=JSONScoring(metric_path="a.b"),
            constraints=TaskConstraints(timeout=999, max_retries=5),
        )
        data = defn.model_dump(mode="json")
        restored = TaskDefinition.model_validate(data)
        assert restored.name == "roundtrip"
        assert isinstance(restored.scoring, JSONScoring)
        assert restored.scoring.metric_path == "a.b"
        assert restored.constraints.timeout == 999

    def test_to_task(self):
        defn = TaskDefinition(
            name="my-task",
            prompt_config=PromptConfig(text="Do stuff."),
        )
        task = defn.to_task()
        assert task.name == "my-task"
        inst = TaskInstance(id="default")
        assert task.prompt(inst) == "Do stuff."


# ── Task base class tests ───────────────────────────────────────


class TestTask:
    def test_default_instances(self):
        """Default instances() yields a single 'default' instance."""
        task = Task()
        insts = list(task.instances())
        assert len(insts) == 1
        assert insts[0].id == "default"

    def test_default_setup_noop(self, tmp_path: Path):
        """Default setup() is a no-op when no command is set."""
        task = Task()
        inst = TaskInstance(id="t1")
        # Should not raise
        task.setup(inst, tmp_path)

    def test_default_prompt(self):
        """Default prompt() returns generic text."""
        task = Task()
        inst = TaskInstance(id="t1")
        p = task.prompt(inst)
        assert len(p) > 0
        assert "tests" in p.lower() or "feature" in p.lower()

    def test_custom_prompt(self):
        defn = TaskDefinition(
            name="custom",
            prompt_config=PromptConfig(text="Custom prompt."),
        )
        task = Task(definition=defn)
        inst = TaskInstance(id="t1")
        assert task.prompt(inst) == "Custom prompt."

    def test_verify_no_command(self, tmp_path: Path):
        """verify() returns score=0.0 when no command is set."""
        task = Task()
        inst = TaskInstance(id="t1")
        result = task.verify(inst, tmp_path)
        assert isinstance(result, VerifyResult)
        assert result.score == 0.0
        assert result.passed is False

    def test_directory_instances(self, tmp_path: Path):
        """instances() scans directories when configured."""
        (tmp_path / "inst_a").mkdir()
        (tmp_path / "inst_b").mkdir()
        (tmp_path / "not_a_dir.txt").write_text("x")

        defn = TaskDefinition(
            name="dir-task",
            instances_config=InstancesConfig(format="directory", source=str(tmp_path)),
        )
        task = Task(definition=defn)
        insts = list(task.instances())
        ids = [i.id for i in insts]
        assert "inst_a" in ids
        assert "inst_b" in ids
        assert "not_a_dir.txt" not in ids

    def test_name_derivation(self):
        """Task._derive_name() converts CamelCase to kebab-case."""

        class ChessEvolve(Task):
            pass

        t = ChessEvolve()
        assert t.name == "chess-evolve"

    def test_from_legacy(self):
        """from_legacy() constructs Task from flat fields."""
        task = Task.from_legacy(
            name="legacy-test",
            test_command="pytest -v",
            test_format="pytest",
        )
        assert task.name == "legacy-test"
        assert isinstance(task.scoring, PytestScoring)
        assert task.definition.verify_config.command == "pytest -v"

    def test_from_legacy_exit_code(self):
        task = Task.from_legacy(
            name="swe",
            test_command="python run.py",
            test_format="exit_code",
        )
        assert isinstance(task.scoring, ExitCodeScoring)

    def test_from_legacy_json(self):
        task = Task.from_legacy(
            name="json-test",
            test_command="python eval.py",
            test_format="json",
            metric_path="result.score",
        )
        assert isinstance(task.scoring, JSONScoring)

    def test_from_legacy_exact_match(self):
        task = Task.from_legacy(
            name="math",
            test_command="python solve.py",
            test_format="exact_match",
        )
        assert isinstance(task.scoring, ExactMatchScoring)


# ── get_evaluator tests ─────────────────────────────────────────


class TestGetEvaluator:
    def test_pytest_evaluator(self):
        defn = TaskDefinition(name="test", scoring=PytestScoring())
        evaluator = defn.get_evaluator()
        from factory.outer_loop.featurebench_evaluator import FeatureBenchEvaluator

        assert isinstance(evaluator, FeatureBenchEvaluator)

    def test_exit_code_evaluator(self):
        defn = TaskDefinition(name="test", scoring=ExitCodeScoring())
        evaluator = defn.get_evaluator()
        from factory.outer_loop.evaluators.exit_code import ExitCodeEvaluator

        assert isinstance(evaluator, ExitCodeEvaluator)

    def test_json_evaluator(self):
        defn = TaskDefinition(name="test", scoring=JSONScoring(metric_path="a.b"))
        evaluator = defn.get_evaluator()
        from factory.outer_loop.evaluators.json_evaluator import JSONEvaluator

        assert isinstance(evaluator, JSONEvaluator)

    def test_exact_match_evaluator(self):
        defn = TaskDefinition(name="test", scoring=ExactMatchScoring())
        evaluator = defn.get_evaluator()
        from factory.outer_loop.evaluators.exact_match import ExactMatchEvaluator

        assert isinstance(evaluator, ExactMatchEvaluator)


# ── Backward compatibility tests ────────────────────────────────


class TestBackwardCompat:
    def test_inner_loop_without_task(self):
        """InnerLoop works without task parameter (backward compat)."""
        from factory.inner_loop import InnerLoop

        loop = InnerLoop(
            project_dir=Path("/tmp/fake"),
            mode="test",
            test_command="echo ok",
            test_format="pytest",
        )
        assert loop.task is None
        assert loop.instance is None
        assert loop.test_command == "echo ok"

    def test_inner_loop_with_task(self, tmp_path: Path):
        """InnerLoop accepts task parameter and derives flat fields."""
        from factory.inner_loop import InnerLoop

        task = Task.from_legacy(
            name="compat-test",
            test_command="pytest -v",
            test_format="exit_code",
        )
        loop = InnerLoop(
            project_dir=tmp_path,
            mode="test",
            task=task,
        )
        assert loop.task is task
        assert loop.test_command == "pytest -v"

    def test_swarm_config_get_task_from_flat(self):
        """SwarmConfig.get_task() constructs Task from flat fields."""
        from factory.outer_loop.models import SwarmConfig

        config = SwarmConfig(
            benchmark="featurebench",
            budget=10,
            test_command="pytest -v",
            test_format="pytest",
        )
        task = config.get_task()
        assert task.name == "featurebench"
        assert isinstance(task.scoring, PytestScoring)

    def test_swarm_config_get_task_explicit(self):
        """SwarmConfig.get_task() returns explicit task when set."""
        from factory.outer_loop.models import SwarmConfig

        config = SwarmConfig(benchmark="test", budget=10)
        explicit_task = Task.from_legacy(name="explicit", test_format="exit_code")
        config.set_task(explicit_task)
        assert config.get_task() is explicit_task

    def test_benchmark_config_to_task(self):
        """BenchmarkConfig.to_task() produces a valid Task."""
        from factory.outer_loop.benchmark_config import BenchmarkConfig

        bc = BenchmarkConfig(
            name="featurebench",
            description="Feature impl bench",
            test_format="pytest",
            test_command="pytest -xvs",
        )
        task = bc.to_task()
        assert task.name == "featurebench"
        assert isinstance(task.scoring, PytestScoring)

    def test_research_target_to_task(self):
        """ResearchTarget.to_task() produces a Task with JSONScoring."""
        from factory.models import ResearchTarget

        rt = ResearchTarget(
            objective="Optimize speed",
            metric="speed_ms",
            target=100.0,
            run_command="python bench.py",
            result_path="metrics.speed",
        )
        task = rt.to_task()
        assert isinstance(task.scoring, JSONScoring)
        assert task.name == "research-speed_ms"


# ── Task independence test ───────────────────────────────────────


class TestTaskIndependence:
    def test_no_forbidden_imports(self):
        """factory/task.py has zero module-level imports from forbidden modules."""
        import ast

        task_path = Path(__file__).parent.parent / "factory" / "task.py"
        tree = ast.parse(task_path.read_text())

        forbidden_prefixes = [
            "factory.workflow",
            "factory.outer_loop",
            "factory.agents",
            "factory.compose",
        ]

        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                # Check it's at module level (not inside a function)
                # ast.walk doesn't give parent info easily, so we check
                # by looking at the top-level body directly
                pass

        # More thorough: only check top-level statements
        for stmt in tree.body:
            if isinstance(stmt, ast.ImportFrom) and stmt.module:
                for prefix in forbidden_prefixes:
                    if stmt.module.startswith(prefix):
                        violations.append(f"line {stmt.lineno}: from {stmt.module} import ...")
            elif isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    for prefix in forbidden_prefixes:
                        if alias.name.startswith(prefix):
                            violations.append(f"line {stmt.lineno}: import {alias.name}")

        assert violations == [], "Forbidden module-level imports in task.py:\n" + "\n".join(violations)
