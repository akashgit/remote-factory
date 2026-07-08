"""Simple linter for contributed workflow directories."""

from __future__ import annotations

import importlib.util
import types
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LintIssue:
    directory: str
    check: str
    message: str


REQUIRED_FILES = ["__init__.py", "workflow.py", "README.md", "test_workflow.py"]

SKIP_DIRS = {"__pycache__"}


def lint_contributed(base_dir: Path) -> list[LintIssue]:
    """Lint all contributed workflow directories under *base_dir*."""
    issues: list[LintIssue] = []

    if not base_dir.is_dir():
        return issues

    for entry in sorted(base_dir.iterdir()):
        if not entry.is_dir() or entry.name in SKIP_DIRS:
            continue
        issues.extend(_lint_directory(entry))

    return issues


def _lint_directory(directory: Path) -> list[LintIssue]:
    issues: list[LintIssue] = []
    name = directory.name

    for filename in REQUIRED_FILES:
        if not (directory / filename).is_file():
            issues.append(LintIssue(name, f"missing-{filename}", f"{filename} not found"))

    workflow_path = directory / "workflow.py"
    if not workflow_path.is_file():
        return issues

    mod = _load_module(workflow_path)
    if mod is None:
        issues.append(LintIssue(name, "load-error", "workflow.py failed to import"))
        return issues

    meta = getattr(mod, "meta", None)
    if not isinstance(meta, dict):
        issues.append(LintIssue(name, "missing-meta", "workflow.py has no module-level meta dict"))
    else:
        for key in ("name", "description"):
            if key not in meta:
                issues.append(LintIssue(name, f"meta-missing-{key}", f"meta dict missing '{key}'"))

    workflow_fn = getattr(mod, "workflow", None)
    if not callable(workflow_fn):
        issues.append(LintIssue(name, "missing-workflow-fn", "workflow.py has no callable workflow()"))
        return issues

    try:
        wf = workflow_fn()
    except Exception as exc:
        issues.append(LintIssue(name, "workflow-call-error", f"workflow() raised: {exc}"))
        return issues

    graph_issues = wf.validate_graph()
    for gi in graph_issues:
        issues.append(LintIssue(name, "graph-invalid", gi))

    return issues


def _load_module(path: Path) -> types.ModuleType | None:
    try:
        spec = importlib.util.spec_from_file_location(f"_lint_{path.parent.name}", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None
