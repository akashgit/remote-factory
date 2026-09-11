"""Tests for factory.task.resolve_task — TOML, Python file, and module:Class resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.task import Task, resolve_task


# ── TOML resolution ─────────────────────────────────────────────


class TestResolveTOML:
    def test_toml_absolute_path(self, tmp_path: Path):
        toml = tmp_path / "my_task.toml"
        toml.write_text(
            '[task]\nname = "my-task"\n'
            '[scoring]\nmethod = "exit_code"\n'
            '[verify]\ncommand = "echo ok"\n'
        )
        task = resolve_task(str(toml))
        assert isinstance(task, Task)
        assert task.name == "my-task"

    def test_toml_relative_to_project(self, tmp_path: Path):
        tasks_dir = tmp_path / ".factory" / "tasks"
        tasks_dir.mkdir(parents=True)
        toml = tasks_dir / "foo.toml"
        toml.write_text(
            '[task]\nname = "foo"\n'
            '[scoring]\nmethod = "exit_code"\n'
            '[verify]\ncommand = "true"\n'
        )
        task = resolve_task(".factory/tasks/foo.toml", project_path=tmp_path)
        assert task.name == "foo"

    def test_toml_missing_raises(self):
        with pytest.raises(FileNotFoundError, match="TOML task file not found"):
            resolve_task("nonexistent.toml")

    def test_toml_with_scoring_contract(self, tmp_path: Path):
        toml = tmp_path / "scored.toml"
        toml.write_text(
            '[task]\nname = "scored"\n'
            '[scoring]\nmethod = "json"\nmetric_path = "stats.accuracy"\n'
            '[verify]\ncommand = "python eval.py"\n'
        )
        task = resolve_task(str(toml))
        assert task.scoring.method == "json"
        assert task.scoring.metric_path == "stats.accuracy"


# ── Python file resolution ──────────────────────────────────────


class TestResolvePythonFile:
    def test_python_file_with_task_subclass(self, tmp_path: Path):
        py_file = tmp_path / "my_task.py"
        py_file.write_text(
            "from factory.task import Task, TaskDefinition, TaskInstance\n"
            "from typing import Iterator\n"
            "\n"
            "class MyCustomTask(Task):\n"
            "    def __init__(self):\n"
            "        super().__init__(TaskDefinition(name='custom'))\n"
            "    def instances(self) -> Iterator[TaskInstance]:\n"
            "        yield TaskInstance(id='default')\n"
        )
        task = resolve_task(str(py_file))
        assert isinstance(task, Task)
        assert task.name == "custom"

    def test_python_file_relative_to_project(self, tmp_path: Path):
        py_file = tmp_path / "tasks" / "simple.py"
        py_file.parent.mkdir(parents=True)
        py_file.write_text(
            "from factory.task import Task, TaskDefinition\n"
            "\n"
            "class SimpleTask(Task):\n"
            "    def __init__(self):\n"
            "        super().__init__(TaskDefinition(name='simple'))\n"
        )
        task = resolve_task("tasks/simple.py", project_path=tmp_path)
        assert task.name == "simple"

    def test_python_file_missing_raises(self):
        with pytest.raises(FileNotFoundError, match="Python task file not found"):
            resolve_task("nonexistent.py")

    def test_python_file_no_task_subclass_raises(self, tmp_path: Path):
        py_file = tmp_path / "empty.py"
        py_file.write_text("x = 42\n")
        with pytest.raises(ImportError, match="No Task subclass found"):
            resolve_task(str(py_file))

    def test_python_file_multiple_subclasses_raises(self, tmp_path: Path):
        py_file = tmp_path / "multi.py"
        py_file.write_text(
            "from factory.task import Task, TaskDefinition\n"
            "\n"
            "class TaskA(Task):\n"
            "    def __init__(self):\n"
            "        super().__init__(TaskDefinition(name='a'))\n"
            "\n"
            "class TaskB(Task):\n"
            "    def __init__(self):\n"
            "        super().__init__(TaskDefinition(name='b'))\n"
        )
        with pytest.raises(ImportError, match="Multiple Task subclasses"):
            resolve_task(str(py_file))


# ── Module:Class resolution (backward compat) ──────────────────


class TestResolveModuleClass:
    def test_module_class_format(self):
        with pytest.raises((ImportError, ValueError)):
            resolve_task("nonexistent.module:FakeTask")

    def test_invalid_format_no_colon(self):
        with pytest.raises(ValueError, match="Expected 'module.path:ClassName'"):
            resolve_task("not_a_file_and_no_colon")


# ── Integration: TOML and Python produce equivalent Tasks ───────


class TestEquivalence:
    def test_toml_and_python_same_task(self, tmp_path: Path):
        toml = tmp_path / "equiv.toml"
        toml.write_text(
            '[task]\nname = "equiv"\n'
            '[scoring]\nmethod = "exit_code"\n'
            '[verify]\ncommand = "pytest -xvs"\n'
            '[constraints]\ntimeout = 300\n'
        )
        py_file = tmp_path / "equiv_task.py"
        py_file.write_text(
            "from factory.task import Task, TaskDefinition, ScoringContract, "
            "TaskConstraints, VerifyConfig\n"
            "\n"
            "class EquivTask(Task):\n"
            "    def __init__(self):\n"
            "        super().__init__(TaskDefinition(\n"
            "            name='equiv',\n"
            "            scoring=ScoringContract(method='exit_code'),\n"
            "            verify_config=VerifyConfig(command='pytest -xvs'),\n"
            "            constraints=TaskConstraints(timeout=300),\n"
            "        ))\n"
        )
        toml_task = resolve_task(str(toml))
        py_task = resolve_task(str(py_file))
        assert toml_task.name == py_task.name
        assert toml_task.scoring.method == py_task.scoring.method
        assert toml_task.definition.verify_config.command == py_task.definition.verify_config.command
        assert toml_task.constraints.timeout == py_task.constraints.timeout
