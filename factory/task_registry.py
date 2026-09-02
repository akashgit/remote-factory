"""3-layer task discovery and loading.

Mirrors WorkflowRegistry exactly:
  1. Built-in: benchmarks/configs/*.toml (lowest priority)
  2. User-global: ~/.factory/tasks/*.toml|*.py
  3. Project-local: .factory/tasks/*.toml|*.py (highest priority, shadows others)

For .py files: must contain a ``meta`` dict with ``name`` and ``description``,
plus a ``task()`` function returning a Task instance.
For .toml files: parsed via TaskDefinition.from_toml().
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

_BUILTIN_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "configs"


@dataclass
class TaskEntry:
    """A discovered task in the registry."""

    name: str
    description: str
    path: str
    source: str  # "builtin", "user", "project"
    scoring: str = ""  # scoring method label
    instances_format: str = ""  # instance format label
    _task_fn: Any = field(default=None, repr=False)


class TaskRegistry:
    """Registry for discovering and loading Task definitions.

    Three-layer discovery with project > user > builtin shadowing.
    """

    _entries: dict[str, TaskEntry] = {}
    _initialized: bool = False

    @classmethod
    def reset(cls) -> None:
        """Reset registry state. Useful for testing."""
        cls._entries.clear()
        cls._initialized = False

    @classmethod
    def discover(cls, project_path: Path | None = None) -> dict[str, TaskEntry]:
        """Discover all tasks from search paths + built-ins.

        Returns name → TaskEntry mapping. Project shadows user shadows builtin.
        """
        cls._entries.clear()

        # Layer 1: built-in tasks (lowest priority)
        cls._discover_toml_dir(_BUILTIN_DIR, "builtin")

        # Layer 2: user-global tasks
        user_dir = Path.home() / ".factory" / "tasks"
        if user_dir.is_dir():
            cls._discover_in_directory(user_dir, "user")

        # Layer 3: project-local tasks (highest priority)
        if project_path:
            project_task_dir = Path(project_path) / ".factory" / "tasks"
            if project_task_dir.is_dir():
                cls._discover_in_directory(project_task_dir, "project")

        cls._initialized = True
        log.info("task_registry.discovered", count=len(cls._entries))
        return cls._entries

    @classmethod
    def _discover_toml_dir(cls, directory: Path, source: str) -> None:
        """Discover .toml files in a directory as task definitions."""
        if not directory.is_dir():
            return
        for toml_file in sorted(directory.glob("*.toml")):
            try:
                from factory.task import TaskDefinition

                defn = TaskDefinition.from_toml(toml_file)
                scoring_label = getattr(defn.scoring, "method", "unknown")
                cls._entries[defn.name] = TaskEntry(
                    name=defn.name,
                    description=defn.description,
                    path=str(toml_file),
                    source=source,
                    scoring=scoring_label,
                    instances_format=defn.instances_config.format,
                    _task_fn=lambda p=toml_file: _load_toml_task(p),
                )
            except Exception as exc:
                log.debug("task_registry.skip_toml", path=str(toml_file), reason=str(exc))

    @classmethod
    def _discover_in_directory(cls, directory: Path, source: str) -> None:
        """Discover .toml and .py files in a directory."""
        if not directory.is_dir():
            return

        # TOML files
        for toml_file in sorted(directory.glob("*.toml")):
            try:
                from factory.task import TaskDefinition

                defn = TaskDefinition.from_toml(toml_file)
                scoring_label = getattr(defn.scoring, "method", "unknown")
                prev = cls._entries.get(defn.name)
                if prev and prev.source not in ("builtin",):
                    log.warning(
                        "task_registry.shadow",
                        name=defn.name,
                        new_source=source,
                        old_source=prev.source,
                    )
                cls._entries[defn.name] = TaskEntry(
                    name=defn.name,
                    description=defn.description,
                    path=str(toml_file),
                    source=source,
                    scoring=scoring_label,
                    instances_format=defn.instances_config.format,
                    _task_fn=lambda p=toml_file: _load_toml_task(p),
                )
            except Exception as exc:
                log.debug("task_registry.skip_toml", path=str(toml_file), reason=str(exc))

        # Python files
        for py_file in sorted(directory.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                meta, task_fn = _load_task_py_file(py_file)
                name = meta["name"]
                prev = cls._entries.get(name)
                if prev and prev.source not in ("builtin",):
                    log.warning(
                        "task_registry.shadow",
                        name=name,
                        new_source=source,
                        old_source=prev.source,
                    )
                cls._entries[name] = TaskEntry(
                    name=name,
                    description=meta.get("description", ""),
                    path=str(py_file),
                    source=source,
                    _task_fn=task_fn,
                )
            except Exception as exc:
                log.debug("task_registry.skip_py", path=str(py_file), reason=str(exc))

    @classmethod
    def load_task(
        cls, name: str, project_path: Path | None = None
    ) -> Any:
        """Load a task by name, discovering if needed.

        Returns a Task instance. Raises KeyError if not found.
        """
        if not cls._entries:
            cls.discover(project_path)

        entry = cls._entries.get(name)
        if entry is None:
            raise KeyError(
                f"No task found for {name!r}. "
                f"Available: {sorted(cls._entries.keys())}"
            )

        if entry._task_fn is None:
            raise ValueError(f"Task {name!r} has no loader function")

        return entry._task_fn()

    @classmethod
    def list_tasks(
        cls, project_path: Path | None = None
    ) -> list[TaskEntry]:
        """List all discovered tasks."""
        if not cls._entries:
            cls.discover(project_path)
        return sorted(
            cls._entries.values(),
            key=lambda e: (e.source != "builtin", e.name),
        )


def _load_toml_task(path: Path) -> Any:
    """Load a Task from a TOML file."""
    from factory.task import Task

    return Task.from_toml(path)


def _load_task_py_file(path: Path) -> tuple[dict[str, Any], Any]:
    """Load a task .py file and extract meta + task function.

    Raises ValueError if the file doesn't have the required exports.
    """
    spec = importlib.util.spec_from_file_location(f"factory_task_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load module from {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(spec.name, None)
        raise ValueError(f"Failed to load {path}: {exc}") from exc

    meta = getattr(module, "meta", None)
    task_fn = getattr(module, "task", None)

    sys.modules.pop(spec.name, None)

    if not isinstance(meta, dict) or "name" not in meta:
        raise ValueError(f"{path} missing 'meta' dict with 'name' key")

    if not callable(task_fn):
        raise ValueError(f"{path} missing 'task()' function")

    return meta, task_fn
