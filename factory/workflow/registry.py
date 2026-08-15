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

from factory.workflow.manifest import WorkflowManifest
from factory.workflow.primitives import Workflow

log = structlog.get_logger()


@dataclass
class WorkflowEntry:
    """A discovered workflow in the registry."""

    name: str
    description: str
    path: str
    source: str  # "builtin", "user", "project", "entry_point"
    manifest: WorkflowManifest | None = None
    package_name: str | None = None
    _workflow_fn: Any = field(default=None, repr=False)


class WorkflowRegistry:
    """Registry for discovering contributed workflows.

    Search paths are scanned for .py files with a `meta` dict and
    `workflow()` function. Built-in workflows from `definitions.py`
    are always available as the lowest-priority source.

    Priority: project > user > entry-points > builtin
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
    def discover(cls, project_path: Path | None = None) -> dict[str, WorkflowEntry]:
        """Discover all workflows from search paths + built-ins.

        Parameters
        ----------
        project_path : Path, optional
            If provided, also searches .factory/workflows/ in this project.

        Returns
        -------
        dict[str, WorkflowEntry]
            Name → entry mapping. Project shadows user shadows entry-point shadows built-in.
        """
        cls._ensure_initialized()
        cls._entries.clear()

        # Layer 1: built-in workflows (lowest priority)
        cls._load_builtins()

        # Layer 2: entry-point workflows
        cls._discover_entry_points()

        # Layer 3: user-global workflows
        for search_path, source in cls._search_paths:
            if source == "user":
                cls._discover_in_directory(search_path, source)

        # Layer 4: project-local workflows (highest priority)
        if project_path:
            project_wf_dir = project_path / ".factory" / "workflows"
            if project_wf_dir.is_dir():
                cls._discover_in_directory(str(project_wf_dir), "project")

        # Layer 5: any explicitly registered paths
        for search_path, source in cls._search_paths:
            if source not in ("user",):
                cls._discover_in_directory(search_path, source)

        log.info("workflow_registry.discovered", count=len(cls._entries))
        return cls._entries

    @classmethod
    def _load_builtins(cls) -> None:
        """Load built-in workflows from definitions.py.

        Uses _get_builtin_registry() so that contributed-workflow modules
        are NOT imported at discovery time.  The callable is stored but
        NOT invoked — the Workflow object is only constructed when
        get_workflow() is called for that specific name.
        """
        from factory.workflow.definitions import _get_builtin_registry

        for name, fn in _get_builtin_registry().items():
            cls._entries[name] = WorkflowEntry(
                name=name,
                description=_get_builtin_description(name),
                path="<builtin>",
                source="builtin",
                _workflow_fn=fn,
            )

    @classmethod
    def _discover_entry_points(cls) -> None:
        """Discover workflows from installed packages via entry points."""
        try:
            from importlib.metadata import entry_points
            eps = entry_points(group="factory.workflows")
        except Exception:
            return

        for ep in eps:
            try:
                module = ep.load()
                meta = getattr(module, "meta", None)
                workflow_fn = getattr(module, "workflow", None)

                if not isinstance(meta, dict) or "name" not in meta:
                    log.debug(
                        "workflow_registry.entry_point_skip",
                        entry_point=ep.name,
                        reason="missing meta dict with name",
                    )
                    continue

                if not callable(workflow_fn):
                    log.debug(
                        "workflow_registry.entry_point_skip",
                        entry_point=ep.name,
                        reason="missing workflow() function",
                    )
                    continue

                name = meta["name"]

                from factory.workflow.manifest import (
                    check_version_compatibility,
                    manifest_from_meta,
                    validate_namespace,
                )

                ns_issues = validate_namespace(name, "entry_point")
                if ns_issues:
                    for issue in ns_issues:
                        log.warning("workflow_registry.namespace_violation", issue=issue)
                    continue

                manifest = manifest_from_meta(meta, strict=False)
                version_issues = check_version_compatibility(manifest)
                if version_issues:
                    for issue in version_issues:
                        log.warning("workflow_registry.version_incompatible", issue=issue)
                    continue

                # Validate graph at discovery time
                graph_issues = _validate_workflow_graph(workflow_fn, name)
                if graph_issues:
                    for issue in graph_issues:
                        log.warning("workflow_registry.graph_invalid", name=name, issue=issue)
                    continue

                # Validate capabilities
                node_types = _get_node_types(workflow_fn, name)
                from factory.workflow.manifest import validate_capabilities
                cap_issues = validate_capabilities(manifest, node_types)
                if cap_issues:
                    for issue in cap_issues:
                        log.warning("workflow_registry.capability_violation", issue=issue)
                    continue

                prev = cls._entries.get(name)
                if prev and prev.source not in ("builtin",):
                    log.warning(
                        "workflow_registry.shadow",
                        name=name,
                        new_source="entry_point",
                        old_source=prev.source,
                    )

                pkg_name = ep.dist.name if ep.dist else None

                cls._entries[name] = WorkflowEntry(
                    name=name,
                    description=meta.get("description", ""),
                    path=f"<entry_point:{ep.name}>",
                    source="entry_point",
                    manifest=manifest,
                    package_name=pkg_name,
                    _workflow_fn=workflow_fn,
                )
                log.debug(
                    "workflow_registry.entry_point_loaded",
                    name=name,
                    entry_point=ep.name,
                    package=pkg_name,
                )
            except Exception as exc:
                log.debug(
                    "workflow_registry.entry_point_error",
                    entry_point=ep.name,
                    error=str(exc),
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

                from factory.workflow.manifest import (
                    check_version_compatibility,
                    manifest_from_meta,
                    validate_namespace,
                )

                ns_issues = validate_namespace(name, source)
                if ns_issues:
                    for issue in ns_issues:
                        log.warning("workflow_registry.namespace_violation", issue=issue)
                    continue

                is_strict = source == "entry_point"
                manifest = manifest_from_meta(meta, strict=is_strict)

                version_issues = check_version_compatibility(manifest)
                if version_issues:
                    for issue in version_issues:
                        log.warning("workflow_registry.version_incompatible", issue=issue)
                    continue

                graph_issues = _validate_workflow_graph(workflow_fn, name)
                if graph_issues:
                    for issue in graph_issues:
                        log.warning("workflow_registry.graph_invalid", name=name, issue=issue)
                    continue

                node_types = _get_node_types(workflow_fn, name)
                from factory.workflow.manifest import validate_capabilities
                cap_issues = validate_capabilities(manifest, node_types)
                if cap_issues:
                    for issue in cap_issues:
                        log.warning("workflow_registry.capability_violation", issue=issue)
                    continue

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
                    manifest=manifest,
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
    def list_workflows(
        cls,
        project_path: Path | None = None,
        *,
        plugins_only: bool = False,
    ) -> list[WorkflowEntry]:
        """List all discovered workflows.

        Parameters
        ----------
        plugins_only : bool
            If True, only return entry-point (plugin) workflows.
        """
        if not cls._entries:
            cls.discover(project_path)
        result = list(cls._entries.values())
        if plugins_only:
            result = [e for e in result if e.source == "entry_point"]
        return sorted(result, key=lambda e: (e.source != "builtin", e.name))


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


def _validate_workflow_graph(workflow_fn: Any, name: str) -> list[str]:
    """Validate the workflow graph at discovery time. Returns issues."""
    try:
        wf = workflow_fn()
        if not isinstance(wf, Workflow):
            return [f"workflow() for '{name}' did not return a Workflow object"]
        return wf.validate_graph()
    except Exception as exc:
        return [f"workflow() for '{name}' raised: {exc}"]


def _get_node_types(workflow_fn: Any, name: str) -> set[str]:
    """Get the set of node type names in a workflow."""
    try:
        wf = workflow_fn()
        if not isinstance(wf, Workflow):
            return set()
        return {type(node).__name__ for node in wf.nodes.values()}
    except Exception:
        return set()
