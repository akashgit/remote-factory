"""Tests for factory.optimization.benchmarks.loader — dynamic benchmark loading."""

from __future__ import annotations

import json

import pytest

from factory.optimization.benchmarks.loader import BenchmarkDefinition, load_benchmark


EXECUTOR_PY = """\
class Executor:
    def execute(self, project_dir, surface, **kwargs):
        return {}
"""

EVALUATOR_PY = """\
class Evaluator:
    def parse(self, artifact_path):
        return {}
    def parse_many(self, artifact_paths):
        return {}
    def get_info(self):
        return {}
"""

CONFIG_JSON = json.dumps({"name": "test-bench", "executor_params": {}, "evaluator_params": {}})


def _write_benchmark(tmp_path, *, config=CONFIG_JSON, executor=EXECUTOR_PY, evaluator=EVALUATOR_PY):
    """Write a complete benchmark directory, returning the path."""
    if config is not None:
        (tmp_path / "config.json").write_text(config)
    if executor is not None:
        (tmp_path / "executor.py").write_text(executor)
    if evaluator is not None:
        (tmp_path / "evaluator.py").write_text(evaluator)
    return tmp_path


class TestLoadValidBenchmark:
    def test_returns_benchmark_definition(self, tmp_path) -> None:
        _write_benchmark(tmp_path)
        defn = load_benchmark(tmp_path)
        assert isinstance(defn, BenchmarkDefinition)
        assert defn.name == "test-bench"
        assert defn.source == "dynamic"
        assert defn.config["name"] == "test-bench"
        assert hasattr(defn.executor_cls, "execute")
        assert hasattr(defn.evaluator_cls, "parse")


class TestMissingConfigJson:
    def test_raises_valueerror(self, tmp_path) -> None:
        _write_benchmark(tmp_path, config=None)
        with pytest.raises(ValueError, match="Missing config.json"):
            load_benchmark(tmp_path)


class TestMissingExecutorPy:
    def test_raises_valueerror(self, tmp_path) -> None:
        _write_benchmark(tmp_path, executor=None)
        with pytest.raises(ValueError, match="Missing executor.py"):
            load_benchmark(tmp_path)


class TestMissingEvaluatorPy:
    def test_raises_valueerror(self, tmp_path) -> None:
        _write_benchmark(tmp_path, evaluator=None)
        with pytest.raises(ValueError, match="Missing evaluator.py"):
            load_benchmark(tmp_path)


class TestMalformedConfigJson:
    def test_raises_valueerror(self, tmp_path) -> None:
        _write_benchmark(tmp_path, config="{not valid json")
        with pytest.raises(ValueError, match="Malformed config.json"):
            load_benchmark(tmp_path)


class TestExecutorMissingExecuteMethod:
    def test_raises_valueerror(self, tmp_path) -> None:
        bad_executor = "class Executor:\n    pass\n"
        _write_benchmark(tmp_path, executor=bad_executor)
        with pytest.raises(ValueError, match="missing required method 'execute'"):
            load_benchmark(tmp_path)


class TestEvaluatorMissingParseMethod:
    def test_raises_valueerror(self, tmp_path) -> None:
        bad_evaluator = "class Evaluator:\n    def get_info(self):\n        return {}\n"
        _write_benchmark(tmp_path, evaluator=bad_evaluator)
        with pytest.raises(ValueError, match="missing required method 'parse'"):
            load_benchmark(tmp_path)


class TestClassNotFoundInModule:
    def test_raises_valueerror(self, tmp_path) -> None:
        wrong_name = "class MyExecutor:\n    def execute(self): pass\n"
        _write_benchmark(tmp_path, executor=wrong_name)
        with pytest.raises(ValueError, match="does not contain a class named 'Executor'"):
            load_benchmark(tmp_path)
