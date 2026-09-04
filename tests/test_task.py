"""Tests for factory/task.py — TaskDefinition, ScoringContract, four hooks, backward compat."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.task import (
    Capability,
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
    _RunResult,
    _build_verify_details,
    _needs_shell,
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
        tc = TaskConstraints(
            timeout=3600,
            max_retries=3,
            required_capabilities=[Capability.CAN_RUN_TESTS],
        )
        assert tc.timeout == 3600
        assert tc.max_retries == 3
        assert tc.required_capabilities == [Capability.CAN_RUN_TESTS]


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
        )
        assert loop.task is None
        assert loop.instance is None
        assert loop.test_command == "echo ok"
        assert loop.test_format == "pytest"  # default when None

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

        # Check top-level statements only (not function-scoped imports)
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


# ── Review fix tests ────────────────────────────────────────────


class TestShellRename:
    """Task.run() was renamed to Task.shell() — shell-command utility."""

    def test_shell_method_exists(self):
        task = Task()
        assert hasattr(task, "shell")
        assert callable(task.shell)

    def test_shell_runs_command(self, tmp_path: Path):
        from factory.task import _RunResult

        task = Task()
        result = task.shell("echo hello", cwd=tmp_path)
        assert isinstance(result, _RunResult)
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_shell_called_by_setup(self, tmp_path: Path):
        """setup() uses shell() internally."""
        marker = tmp_path / "setup_ran"
        defn = TaskDefinition(
            name="test",
            setup_config=__import__("factory.task", fromlist=["SetupConfig"]).SetupConfig(
                command=f"touch {marker}",
            ),
        )
        task = Task(definition=defn)
        inst = TaskInstance(id="t1")
        task.setup(inst, tmp_path)
        assert marker.exists()


class TestTaskRun:
    """New Task.run() — unified execution entrypoint."""

    def test_run_returns_verify_result(self, tmp_path: Path, monkeypatch):
        """run() returns a VerifyResult after setup → subprocess → verify."""
        import subprocess as sp

        calls: list[list[str]] = []

        def fake_run(*args, **kwargs):
            if isinstance(args[0], list):
                calls.append(args[0])
            return sp.CompletedProcess(args=args, returncode=0)

        monkeypatch.setattr(sp, "run", fake_run)

        defn = TaskDefinition(
            name="test-run",
            verify_config=__import__("factory.task", fromlist=["VerifyConfig"]).VerifyConfig(
                command="echo done",
            ),
            scoring=ExitCodeScoring(),
        )
        task = Task(definition=defn)
        inst = TaskInstance(id="t1")

        result = task.run(inst, tmp_path)
        assert isinstance(result, VerifyResult)

    def test_run_writes_prompt_to_temp_file(self, tmp_path: Path, monkeypatch):
        """run() writes prompt to a temp file and passes --prompt <file>."""
        import subprocess as sp

        captured_cmds: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list):
                captured_cmds.append(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(sp, "run", fake_run)

        defn = TaskDefinition(
            name="test-prompt",
            prompt_config=__import__("factory.task", fromlist=["PromptConfig"]).PromptConfig(
                text="My custom prompt",
            ),
            scoring=ExitCodeScoring(),
        )
        task = Task(definition=defn)
        inst = TaskInstance(id="t1")
        task.run(inst, tmp_path)

        ceo_calls = [c for c in captured_cmds if "factory" in " ".join(c)]
        assert len(ceo_calls) >= 1
        ceo_cmd = ceo_calls[0]
        assert "--prompt" in ceo_cmd

    def test_run_cleans_up_prompt_file(self, tmp_path: Path, monkeypatch):
        """Prompt temp file is cleaned up after run()."""
        import subprocess as sp

        prompt_path_holder: list[str] = []

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list) and "--prompt" in cmd:
                idx = cmd.index("--prompt")
                prompt_path_holder.append(cmd[idx + 1])
            return sp.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(sp, "run", fake_run)

        task = Task(definition=TaskDefinition(
            name="cleanup-test", scoring=ExitCodeScoring(),
        ))
        task.run(TaskInstance(id="t1"), tmp_path)

        if prompt_path_holder:
            assert not Path(prompt_path_holder[0]).exists()


class TestNeedsShell:
    """Item 1: _needs_shell detects shell operators."""

    def test_simple_command(self):
        assert _needs_shell("pytest -xvs") is False

    def test_and_operator(self):
        assert _needs_shell("cd /tmp && pytest") is True

    def test_or_operator(self):
        assert _needs_shell("test -f x || echo no") is True

    def test_semicolon(self):
        assert _needs_shell("echo a; echo b") is True

    def test_pipe(self):
        assert _needs_shell("grep foo | wc -l") is True


class TestTrustedExpectedAnswer:
    """Item 2: _parse_exact_match_verify reads from instance.path first."""

    def test_reads_from_instance_path(self, tmp_path: Path):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        (instance_dir / "expected_answer.txt").write_text("trusted")
        (workspace / "expected_answer.txt").write_text("forged")

        from factory.task import ExactMatchScoring, _RunResult

        result = _RunResult(returncode=0, stdout="trusted\n", stderr="")
        scoring = ExactMatchScoring()
        inst = TaskInstance(id="t1", path=instance_dir)

        vr = Task._parse_exact_match_verify(result, scoring, workspace, inst)
        assert vr.passed is True
        assert vr.score == 1.0

    def test_falls_back_to_workspace(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "expected_answer.txt").write_text("answer")

        from factory.task import ExactMatchScoring, _RunResult

        result = _RunResult(returncode=0, stdout="answer\n", stderr="")
        scoring = ExactMatchScoring()

        vr = Task._parse_exact_match_verify(result, scoring, workspace, None)
        assert vr.passed is True


class TestUnknownScoringRaises:
    """Item 5: from_toml raises ValueError on unknown scoring method."""

    def test_unknown_method(self, tmp_path: Path):
        toml_content = """
[task]
name = "bad-task"

[scoring]
method = "bogus"
"""
        f = tmp_path / "bad.toml"
        f.write_text(toml_content)
        with pytest.raises(ValueError, match="Unknown scoring method"):
            TaskDefinition.from_toml(f)

    def test_binary_alias_removed(self, tmp_path: Path):
        toml_content = """
[task]
name = "bin-task"

[scoring]
method = "binary"
"""
        f = tmp_path / "bin.toml"
        f.write_text(toml_content)
        with pytest.raises(ValueError, match="Unknown scoring method"):
            TaskDefinition.from_toml(f)


class TestSwarmConfigCaching:
    """Item 6: SwarmConfig.get_task() returns the same object on repeated calls."""

    def test_caches_result(self):
        from factory.outer_loop.models import SwarmConfig

        config = SwarmConfig(
            benchmark="test-bench",
            budget=10,
            test_command="pytest",
        )
        t1 = config.get_task()
        t2 = config.get_task()
        assert t1 is t2


class TestRegistryProjectPath:
    """Item 7: TaskRegistry rediscovers when project_path changes."""

    def test_different_project_rediscovers(self, tmp_path: Path):
        from factory.task_registry import TaskRegistry

        proj_a = tmp_path / "a"
        proj_b = tmp_path / "b"
        for p in (proj_a, proj_b):
            task_dir = p / ".factory" / "tasks"
            task_dir.mkdir(parents=True)
            (task_dir / "local.toml").write_text(f"""
[task]
name = "local-{p.name}"
description = "From project {p.name}"

[scoring]
method = "exit_code"
""")

        TaskRegistry.reset()
        TaskRegistry.discover(proj_a)
        assert "local-a" in TaskRegistry._entries

        TaskRegistry.discover(proj_b)
        assert "local-b" in TaskRegistry._entries


class TestTestFormatSentinel:
    """Item 9: InnerLoop test_format sentinel — only infer when None."""

    def test_explicit_pytest_preserved_with_exit_code_task(self, tmp_path: Path):
        from factory.inner_loop import InnerLoop

        task = Task.from_legacy(
            name="exit-test",
            test_command="python run.py",
            test_format="exit_code",
        )
        loop = InnerLoop(
            project_dir=tmp_path,
            mode="test",
            task=task,
            test_format="pytest",
        )
        assert loop.test_format == "pytest"

    def test_none_infers_from_task(self, tmp_path: Path):
        from factory.inner_loop import InnerLoop

        task = Task.from_legacy(
            name="exit-test",
            test_command="python run.py",
            test_format="exit_code",
        )
        loop = InnerLoop(
            project_dir=tmp_path,
            mode="test",
            task=task,
        )
        assert loop.test_format == "exit_code"


class TestVersionField:
    """Item 10a: TaskDefinition has a version field."""

    def test_default_empty(self):
        defn = TaskDefinition(name="t")
        assert defn.version == ""

    def test_from_toml_parses_version(self, tmp_path: Path):
        toml_content = """
[task]
name = "versioned"
version = "1.2.3"
"""
        f = tmp_path / "v.toml"
        f.write_text(toml_content)
        defn = TaskDefinition.from_toml(f)
        assert defn.version == "1.2.3"


class TestCapabilityTyping:
    """Item 10b: required_capabilities uses Capability enum."""

    def test_pydantic_validates_capabilities(self):
        tc = TaskConstraints(
            required_capabilities=[Capability.CAN_RUN_TESTS, Capability.HAS_BUILDER],
        )
        assert all(isinstance(c, Capability) for c in tc.required_capabilities)

    def test_string_coerced_to_capability(self):
        tc = TaskConstraints(required_capabilities=["can_run_tests"])
        assert tc.required_capabilities[0] == Capability.CAN_RUN_TESTS

    def test_invalid_capability_raises(self):
        with pytest.raises(Exception):
            TaskConstraints(required_capabilities=["nonexistent"])


class TestEvaluatorRefAlignment:
    """Item 4: EvaluatorRef('pytest') resolves to FeatureBenchEvaluator."""

    def test_shorthand_matches_isinstance_path(self):
        from factory.outer_loop.featurebench_evaluator import FeatureBenchEvaluator
        from factory.task import EvaluatorRef

        evaluator = EvaluatorRef(ref="pytest").resolve()
        assert isinstance(evaluator, FeatureBenchEvaluator)


class TestTaskRunSubprocess:
    """Task.run() subprocess path — covers lines 516-547."""

    def test_run_calls_hooks_in_order(self, tmp_path: Path, monkeypatch):
        """run() calls setup(), prompt(), verify() in the correct order."""
        import subprocess as sp

        call_order: list[str] = []

        class OrderTrackingTask(Task):
            def setup(self, instance, workspace):
                call_order.append("setup")

            def prompt(self, instance):
                call_order.append("prompt")
                return "test prompt"

            def verify(self, instance, workspace):
                call_order.append("verify")
                return VerifyResult(passed=True, score=1.0)

        monkeypatch.setattr(sp, "run", lambda *a, **k: sp.CompletedProcess(args=a, returncode=0))

        task = OrderTrackingTask(definition=TaskDefinition(name="order-test"))
        result = task.run(TaskInstance(id="t1"), tmp_path)

        assert call_order == ["setup", "prompt", "verify"]
        assert result.passed is True

    def test_run_passes_correct_command_args(self, tmp_path: Path, monkeypatch):
        """run() passes correct command args to subprocess."""
        import subprocess as sp
        import sys

        captured_cmd: list[str] = []

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list) and len(cmd) > 3:
                captured_cmd.extend(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(sp, "run", fake_run)

        task = Task(definition=TaskDefinition(name="args-test", scoring=ExitCodeScoring()))
        task.run(TaskInstance(id="t1"), tmp_path)

        assert captured_cmd[0] == sys.executable
        assert captured_cmd[1:3] == ["-m", "factory"]
        assert "ceo" in captured_cmd
        assert str(tmp_path) in captured_cmd
        assert "--mode" in captured_cmd
        assert "--headless" in captured_cmd
        assert "--no-worktree" in captured_cmd

    def test_run_defaults_to_improve_mode(self, tmp_path: Path, monkeypatch):
        """run() defaults to 'improve' mode when workflow is None."""
        import subprocess as sp

        captured_cmd: list[str] = []

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list) and "--mode" in cmd:
                captured_cmd.extend(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(sp, "run", fake_run)

        task = Task(definition=TaskDefinition(name="mode-test", scoring=ExitCodeScoring()))
        task.run(TaskInstance(id="t1"), tmp_path, workflow=None)

        mode_idx = captured_cmd.index("--mode")
        assert captured_cmd[mode_idx + 1] == "improve"

    def test_run_uses_workflow_name_as_mode(self, tmp_path: Path, monkeypatch):
        """run() uses workflow.name as the mode when workflow is provided."""
        import subprocess as sp
        from unittest.mock import MagicMock

        captured_cmd: list[str] = []

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list) and "--mode" in cmd:
                captured_cmd.extend(cmd)
            return sp.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(sp, "run", fake_run)

        workflow = MagicMock()
        workflow.name = "research"

        task = Task(definition=TaskDefinition(name="wf-test", scoring=ExitCodeScoring()))
        task.run(TaskInstance(id="t1"), tmp_path, workflow=workflow)

        mode_idx = captured_cmd.index("--mode")
        assert captured_cmd[mode_idx + 1] == "research"

    def test_run_handles_timeout_expired(self, tmp_path: Path, monkeypatch):
        """run() handles subprocess.TimeoutExpired gracefully."""
        import subprocess as sp

        call_count = {"subprocess": 0}

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list) and "factory" in str(cmd):
                call_count["subprocess"] += 1
                raise sp.TimeoutExpired(cmd=cmd, timeout=600)
            return sp.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(sp, "run", fake_run)

        verify_called = {"called": False}

        class TimeoutTask(Task):
            def verify(self, instance, workspace):
                verify_called["called"] = True
                return VerifyResult(passed=False, score=0.0)

        task = TimeoutTask(definition=TaskDefinition(name="timeout-test", scoring=ExitCodeScoring()))
        result = task.run(TaskInstance(id="t1"), tmp_path)

        assert call_count["subprocess"] == 1
        assert verify_called["called"]
        assert isinstance(result, VerifyResult)

    def test_run_handles_generic_exception(self, tmp_path: Path, monkeypatch):
        """run() handles generic exceptions and still calls verify()."""
        import subprocess as sp

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list) and "factory" in str(cmd):
                raise OSError("connection refused")
            return sp.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(sp, "run", fake_run)

        verify_called = {"called": False}

        class ErrorTask(Task):
            def verify(self, instance, workspace):
                verify_called["called"] = True
                return VerifyResult(passed=False, score=0.0)

        task = ErrorTask(definition=TaskDefinition(name="err-test", scoring=ExitCodeScoring()))
        result = task.run(TaskInstance(id="t1"), tmp_path)

        assert verify_called["called"]
        assert isinstance(result, VerifyResult)

    def test_run_writes_and_cleans_prompt_file(self, tmp_path: Path, monkeypatch):
        """run() writes prompt to a temp file and cleans it up."""
        import subprocess as sp

        prompt_file_during_run: list[Path] = []
        prompt_existed: list[bool] = []

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list) and "--prompt" in cmd:
                idx = cmd.index("--prompt")
                p = Path(cmd[idx + 1])
                prompt_file_during_run.append(p)
                prompt_existed.append(p.exists())
            return sp.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(sp, "run", fake_run)

        defn = TaskDefinition(
            name="prompt-test",
            prompt_config=PromptConfig(text="Hello world"),
            scoring=ExitCodeScoring(),
        )
        task = Task(definition=defn)
        task.run(TaskInstance(id="t1"), tmp_path)

        assert len(prompt_file_during_run) == 1
        assert prompt_existed[0] is True
        assert not prompt_file_during_run[0].exists()


class TestTaskCreateNameDerivation:
    """Item 13: _cmd_task_create handles URLs with trailing slashes."""

    def test_url_trailing_slash(self, tmp_path: Path):
        import argparse

        from factory.cli.task import _cmd_task_create

        args = argparse.Namespace(
            source="https://github.com/user/my-repo/",
            project=str(tmp_path),
        )
        _cmd_task_create(args)
        assert (tmp_path / ".factory" / "tasks" / "my-repo.toml").exists()

    def test_url_with_query_params(self, tmp_path: Path):
        import argparse

        from factory.cli.task import _cmd_task_create

        args = argparse.Namespace(
            source="https://github.com/user/my-repo?tab=code",
            project=str(tmp_path),
        )
        _cmd_task_create(args)
        assert (tmp_path / ".factory" / "tasks" / "my-repo.toml").exists()


# ── _build_verify_details tests ─────────────────────────────────


class TestBuildVerifyDetails:
    def test_scoring_contract_always_present(self):
        result = _RunResult(returncode=0, stdout="ok", stderr="")
        details = _build_verify_details("PytestScoring", result, True)
        assert details["scoring_contract"] == "PytestScoring"

    def test_returncode_always_present(self):
        result = _RunResult(returncode=42, stdout="", stderr="")
        details = _build_verify_details("ExitCodeScoring", result, False)
        assert details["returncode"] == 42

    def test_passed_true_omits_stdout_stderr(self):
        result = _RunResult(returncode=0, stdout="output", stderr="err")
        details = _build_verify_details("PytestScoring", result, True)
        assert "stdout" not in details
        assert "stderr" not in details

    def test_passed_false_includes_stdout_stderr(self):
        result = _RunResult(returncode=1, stdout="fail output", stderr="fail err")
        details = _build_verify_details("PytestScoring", result, False)
        assert details["stdout"] == "fail output"
        assert details["stderr"] == "fail err"

    def test_truncation_at_2000_chars(self):
        long_out = "x" * 3000
        long_err = "y" * 3000
        result = _RunResult(returncode=1, stdout=long_out, stderr=long_err)
        details = _build_verify_details("ExitCodeScoring", result, False)
        assert len(details["stdout"]) == 2000
        assert len(details["stderr"]) == 2000

    def test_extra_kwargs_merged(self):
        result = _RunResult(returncode=0, stdout="", stderr="")
        details = _build_verify_details(
            "JSONScoring", result, True,
            metric_path="score", raw_value=0.95,
        )
        assert details["metric_path"] == "score"
        assert details["raw_value"] == 0.95

    def test_all_scoring_names(self):
        result = _RunResult(returncode=0, stdout="", stderr="")
        for name in ("PytestScoring", "ExitCodeScoring", "JSONScoring", "ExactMatchScoring", "unknown"):
            details = _build_verify_details(name, result, True)
            assert details["scoring_contract"] == name
            assert "returncode" in details


class TestVerifyDetailsConsistency:
    """Verify that each scoring branch in Task.verify() populates details consistently."""

    def _make_task(self, scoring, verify_cmd="echo ok"):
        from factory.task import VerifyConfig
        defn = TaskDefinition(
            name="test",
            scoring=scoring,
            verify_config=VerifyConfig(command=verify_cmd),
        )
        return Task(definition=defn)

    def test_pytest_scoring_has_scoring_contract(self, tmp_path: Path):
        task = self._make_task(PytestScoring(), verify_cmd="echo '1 passed'")
        result = task.verify(TaskInstance(id="t1"), tmp_path)
        assert result.details["scoring_contract"] == "PytestScoring"
        assert "returncode" in result.details

    def test_exit_code_scoring_has_scoring_contract(self, tmp_path: Path):
        task = self._make_task(ExitCodeScoring())
        result = task.verify(TaskInstance(id="t1"), tmp_path)
        assert result.details["scoring_contract"] == "ExitCodeScoring"
        assert "returncode" in result.details

    def test_exit_code_failure_includes_stdout_stderr(self, tmp_path: Path):
        task = self._make_task(ExitCodeScoring(), verify_cmd="false")
        result = task.verify(TaskInstance(id="t1"), tmp_path)
        assert result.details["scoring_contract"] == "ExitCodeScoring"
        assert "stdout" in result.details
        assert "stderr" in result.details

    def test_json_scoring_has_scoring_contract(self, tmp_path: Path):
        task = self._make_task(
            JSONScoring(metric_path="score"),
            verify_cmd='echo \'{"score": 0.8}\'',
        )
        result = task.verify(TaskInstance(id="t1"), tmp_path)
        assert result.details["scoring_contract"] == "JSONScoring"
        assert result.details["metric_path"] == "score"
        assert result.details["raw_value"] == 0.8
        assert "returncode" in result.details

    def test_json_scoring_failure_has_scoring_contract(self, tmp_path: Path):
        task = self._make_task(
            JSONScoring(metric_path="score"),
            verify_cmd="echo not-json",
        )
        result = task.verify(TaskInstance(id="t1"), tmp_path)
        assert result.details["scoring_contract"] == "JSONScoring"
        assert result.details["error"] == "json_parse_failed"
        assert "returncode" in result.details

    def test_exact_match_scoring_has_scoring_contract(self, tmp_path: Path):
        (tmp_path / "expected_answer.txt").write_text("hello")
        task = self._make_task(ExactMatchScoring(), verify_cmd="echo hello")
        result = task.verify(TaskInstance(id="t1"), tmp_path)
        assert result.details["scoring_contract"] == "ExactMatchScoring"
        assert result.details["matched"] is True
        assert "returncode" in result.details

    def test_exact_match_failure_includes_stdout_stderr(self, tmp_path: Path):
        (tmp_path / "expected_answer.txt").write_text("expected")
        task = self._make_task(ExactMatchScoring(), verify_cmd="echo wrong")
        result = task.verify(TaskInstance(id="t1"), tmp_path)
        assert result.details["scoring_contract"] == "ExactMatchScoring"
        assert result.details["matched"] is False
        assert "stdout" in result.details
        assert "stderr" in result.details

    def test_exact_match_missing_answer_file(self, tmp_path: Path):
        task = self._make_task(ExactMatchScoring(), verify_cmd="echo hello")
        result = task.verify(TaskInstance(id="t1"), tmp_path)
        assert result.details["scoring_contract"] == "ExactMatchScoring"
        assert result.details["error"] == "expected_answer_file_missing"
