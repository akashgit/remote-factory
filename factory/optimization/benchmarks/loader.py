"""Dynamic benchmark loader — imports executor/evaluator from a directory at runtime."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BenchmarkDefinition:
    """A dynamically loaded benchmark with its executor and evaluator classes."""

    name: str
    executor_cls: type
    evaluator_cls: type
    config: dict[str, Any] = field(default_factory=dict)
    source: str = "dynamic"


def load_benchmark(benchmark_dir: Path) -> BenchmarkDefinition:
    """Load a benchmark from a directory containing config.json, executor.py, and evaluator.py.

    Raises ValueError on missing files, malformed JSON, missing classes,
    or protocol violations.
    """
    benchmark_dir = benchmark_dir.resolve()

    config_path = benchmark_dir / "config.json"
    if not config_path.is_file():
        raise ValueError(f"Missing config.json in {benchmark_dir}")

    try:
        config = json.loads(config_path.read_text())
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Malformed config.json in {benchmark_dir}: {exc}") from exc

    executor_path = benchmark_dir / "executor.py"
    if not executor_path.is_file():
        raise ValueError(f"Missing executor.py in {benchmark_dir}")

    evaluator_path = benchmark_dir / "evaluator.py"
    if not evaluator_path.is_file():
        raise ValueError(f"Missing evaluator.py in {benchmark_dir}")

    executor_cls = _load_class_from_file(executor_path, "Executor")
    _validate_protocol(executor_cls, "execute")

    evaluator_cls = _load_class_from_file(evaluator_path, "Evaluator")
    _validate_protocol(evaluator_cls, ["parse", "parse_many", "get_info"])

    name = config.get("name", benchmark_dir.name)

    return BenchmarkDefinition(
        name=name,
        executor_cls=executor_cls,
        evaluator_cls=evaluator_cls,
        config=config,
        source="dynamic",
    )


def _load_class_from_file(path: Path, class_name: str) -> type:
    """Import a single class from a Python file using importlib."""
    module_name = f"factory_benchmark_{path.stem}_{id(path)}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot create module spec from {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise ValueError(f"Failed to load {path}: {exc}") from exc

    cls = getattr(module, class_name, None)
    sys.modules.pop(module_name, None)

    if cls is None:
        raise ValueError(f"{path} does not contain a class named '{class_name}'")

    return cls


def _validate_protocol(cls: type, methods: str | list[str]) -> None:
    """Check that cls has the required callable methods."""
    if isinstance(methods, str):
        methods = [methods]
    for method in methods:
        attr = getattr(cls, method, None)
        if attr is None or not callable(attr):
            raise ValueError(
                f"{cls.__name__} is missing required method '{method}'"
            )
