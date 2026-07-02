"""Workflow registry for discovering and loading contributed workflows.

Follows the same search-path pattern as sdg_hub's FlowRegistry:
register directories, auto-discover workflow files within them.

A workflow file is any .py file containing:
  - A `meta` dict with at least `name` and `description`
  - A `workflow()` function returning a Workflow object
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from factory.workflow.primitives import Workflow

log = structlog.get_logger()


@dataclass
class WorkflowEntry:
    """A discovered workflow in the registry."""

    name: str
    description: str
    path: str
    source: str  # "builtin", "user", "project"
    _workflow_fn: Any = field(default=None, repr=False)


class WorkflowRegistry:
    """Registry for discovering contributed workflows.

    Search paths are scanned for .py files with a `meta` dict and
    `workflow()` function. Built-in workflows from `definitions.py`
    are always available as the lowest-priority source.
    """

    _entries: dict[str, WorkflowEntry] = {}
    _search_paths: list[tuple[str, str]] = []  # (path, source_label)
    _initialized: bool = False

    @classmethod
    def reset(cls) -> None:
        """Reset registry state. Useful for testing."""
        cls._entries.clear()
        cls._search_paths.clear()
        cls._initialized = False

    @classmethod
    def _ensure_initialized(cls) -> None:
        """Register default search paths on first access."""
        if cls._initialized:
            return

        # User-global workflows
        user_dir = Path.home() / ".factory" / "workflows"
        if user_dir.is_dir():
            cls._search_paths.append((str(user_dir), "user"))
            log.debug("workflow_registry.search_path", path=str(user_dir), source="user")

        cls._initialized = True

    @classmethod
    def register_search_path(cls, path: str, source: str = "project") -> None:
        """Add a directory to search for workflow files.

        Parameters
        ----------
        path : str
            Path to directory containing workflow .py files.
        source : str
            Label for provenance ("project", "user", etc.).
        """
        resolved = str(Path(path).resolve())
        existing = {p for p, _ in cls._search_paths}
        if resolved not in existing:
            cls._search_paths.append((resolved, source))
            log.debug("workflow_registry.search_path", path=resolved, source=source)

    @classmethod
    def discover(cls, project_path: Path | None = None) -> dict[str, WorkflowEntry]:
        """Discover all workflows from search paths + built-ins.

        Parameters
        ----------
        project_path : Path, optional
            If provided, also searches .factory/workflows/ in this project.

        Returns
        -------
        dict[str, WorkflowEntry]
            Name → entry mapping. Project shadows user shadows built-in.
        """
        cls._ensure_initialized()
        cls._entries.clear()

        # Layer 1: built-in workflows (lowest priority)
        cls._load_builtins()

        # Layer 2: user-global workflows
        for search_path, source in cls._search_paths:
            if source == "user":
                cls._discover_in_directory(search_path, source)

        # Layer 3: project-local workflows (highest priority)
        if project_path:
            project_wf_dir = project_path / ".factory" / "workflows"
            if project_wf_dir.is_dir():
                cls._discover_in_directory(str(project_wf_dir), "project")

        # Layer 4: any explicitly registered paths
        for search_path, source in cls._search_paths:
            if source not in ("user",):
                cls._discover_in_directory(search_path, source)

        log.info("workflow_registry.discovered", count=len(cls._entries))
        return cls._entries

    @classmethod
    def _load_builtins(cls) -> None:
        """Load built-in workflows from definitions.py."""
        from factory.workflow.definitions import register_all

        for name, wf in register_all().items():
            cls._entries[name] = WorkflowEntry(
                name=name,
                description=_get_builtin_description(name),
                path="<builtin>",
                source="builtin",
                _workflow_fn=lambda _wf=wf: _wf,
            )

    @classmethod
    def _discover_in_directory(cls, directory: str, source: str) -> None:
        """Discover workflow files in a directory."""
        path = Path(directory)
        if not path.is_dir():
            return

        for py_file in sorted(path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                meta, workflow_fn = _load_workflow_file(py_file)
                name = meta["name"]
                prev = cls._entries.get(name)
                if prev and prev.source != "builtin":
                    log.warning(
                        "workflow_registry.shadow",
                        name=name,
                        new_source=source,
                        old_source=prev.source,
                    )
                cls._entries[name] = WorkflowEntry(
                    name=name,
                    description=meta.get("description", ""),
                    path=str(py_file),
                    source=source,
                    _workflow_fn=workflow_fn,
                )
                log.debug(
                    "workflow_registry.loaded",
                    name=name,
                    path=str(py_file),
                    source=source,
                )
            except Exception as exc:
                log.debug("workflow_registry.skip", path=str(py_file), reason=str(exc))

    @classmethod
    def get_workflow(cls, name: str, project_path: Path | None = None) -> Workflow | None:
        """Get a workflow by name, discovering if needed.

        Returns None if not found.
        """
        if not cls._entries:
            cls.discover(project_path)

        entry = cls._entries.get(name)
        if entry is None:
            return None

        if entry._workflow_fn is None:
            return None

        return entry._workflow_fn()

    @classmethod
    def list_workflows(cls, project_path: Path | None = None) -> list[WorkflowEntry]:
        """List all discovered workflows."""
        if not cls._entries:
            cls.discover(project_path)
        return sorted(cls._entries.values(), key=lambda e: (e.source != "builtin", e.name))


def _load_workflow_file(path: Path) -> tuple[dict[str, Any], Any]:
    """Load a workflow .py file and extract meta + workflow function.

    Raises ValueError if the file doesn't have the required exports.
    """
    spec = importlib.util.spec_from_file_location(f"factory_workflow_{path.stem}", path)
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
    workflow_fn = getattr(module, "workflow", None)

    # Clean up sys.modules — we only need the extracted objects
    sys.modules.pop(spec.name, None)

    if not isinstance(meta, dict) or "name" not in meta:
        raise ValueError(f"{path} missing 'meta' dict with 'name' key")

    if not callable(workflow_fn):
        raise ValueError(f"{path} missing 'workflow()' function")

    return meta, workflow_fn


def _get_builtin_description(name: str) -> str:
    """Get description for a built-in workflow from WORKFLOW_META."""
    from factory.workflow.skill_export import WORKFLOW_META

    meta = WORKFLOW_META.get(name, {})
    return str(meta.get("description", f"Built-in {name} workflow"))
