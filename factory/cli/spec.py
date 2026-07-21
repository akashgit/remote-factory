"""Spec subcommands — generate, validate, scope, update, impact."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from factory.cli._helpers import _emit_cli_event, _run


def cmd_spec_generate(args: argparse.Namespace) -> int:
    """Generate a repo spec for a project."""
    from factory.spec.generate import generate_spec

    project_path = Path(args.path).resolve()
    if not project_path.is_dir():
        print(f"Error: not a directory: {project_path}", file=sys.stderr)
        return 1

    _emit_cli_event(project_path, "spec.generate.started", {"path": str(project_path)})
    try:
        result_path = _run(generate_spec(project_path))
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        _emit_cli_event(project_path, "spec.generate.failed", {"error": str(exc)[:200]})
        return 1

    _emit_cli_event(project_path, "spec.generate.completed", {"output": str(result_path)})
    print(f"Repo spec generated: {result_path}")
    return 0


def cmd_spec_validate(args: argparse.Namespace) -> int:
    """Validate a repo spec against the actual project."""
    from factory.discovery.spec import resolve_spec
    from factory.spec.ops import validate_spec

    project_path = Path(args.path).resolve()
    if not project_path.is_dir():
        print(f"Error: not a directory: {project_path}", file=sys.stderr)
        return 1

    spec_path = resolve_spec(project_path)
    if spec_path is None:
        print("Error: no repo spec found (run 'factory spec generate' first)", file=sys.stderr)
        return 1

    _emit_cli_event(project_path, "spec.validate.started", {"path": str(project_path)})
    try:
        report, is_valid = _run(validate_spec(project_path))
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        _emit_cli_event(project_path, "spec.validate.failed", {"error": str(exc)[:200]})
        return 1

    output_path = project_path / ".factory" / "spec_validation.md"
    _emit_cli_event(
        project_path,
        "spec.validate.completed",
        {
            "is_valid": is_valid,
            "output": str(output_path),
        },
    )

    print(report)
    print(f"\nReport: {output_path}")
    return 0 if is_valid else 1


def cmd_spec_scope(args: argparse.Namespace) -> int:
    """Scope a diff against the existing repo spec."""
    from factory.discovery.spec import resolve_spec
    from factory.spec.ops import scope_diff

    project_path = Path(args.path).resolve()
    if not project_path.is_dir():
        print(f"Error: not a directory: {project_path}", file=sys.stderr)
        return 1

    spec_path = resolve_spec(project_path)
    if spec_path is None:
        print("Error: no repo spec found (run 'factory spec generate' first)", file=sys.stderr)
        return 1

    exp_id = getattr(args, "experiment", None)
    _emit_cli_event(project_path, "spec.scope.started", {"path": str(project_path)})
    try:
        scope_text = _run(scope_diff(project_path, experiment_id=exp_id))
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        _emit_cli_event(project_path, "spec.scope.failed", {"error": str(exc)[:200]})
        return 1

    output_path = project_path / ".factory" / "spec_update_scope.md"
    _emit_cli_event(
        project_path,
        "spec.scope.completed",
        {"output": str(output_path)},
    )

    print(scope_text)
    print(f"\nReport: {output_path}")
    return 0


def cmd_spec_update(args: argparse.Namespace) -> int:
    """Update a repo spec based on changes since last spec commit."""
    from factory.discovery.spec import resolve_spec
    from factory.spec.ops import update_spec

    project_path = Path(args.path).resolve()
    if not project_path.is_dir():
        print(f"Error: not a directory: {project_path}", file=sys.stderr)
        return 1

    spec_path = resolve_spec(project_path)
    if spec_path is None:
        print("Error: no repo spec found (run 'factory spec generate' first)", file=sys.stderr)
        return 1

    _emit_cli_event(project_path, "spec.update.started", {"path": str(project_path)})
    try:
        result_path = _run(update_spec(project_path))
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        _emit_cli_event(project_path, "spec.update.failed", {"error": str(exc)[:200]})
        return 1

    _emit_cli_event(project_path, "spec.update.completed", {"output": str(result_path)})
    print(f"Repo spec updated: {result_path}")
    return 0


def cmd_spec_impact(args: argparse.Namespace) -> int:
    """Print the impact subgraph for a module from the repo spec."""
    from factory.discovery.spec import resolve_spec
    from factory.spec.ops import get_impact

    project_path = Path(args.project).resolve()
    if not project_path.is_dir():
        print(f"Error: not a directory: {project_path}", file=sys.stderr)
        return 1

    spec_path = resolve_spec(project_path)
    if spec_path is None:
        print("Error: no repo spec found (run 'factory spec generate' first)", file=sys.stderr)
        return 1

    try:
        snippet = _run(get_impact(args.module, project_path))
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(snippet)
    return 0


def cmd_spec_resolve(args: argparse.Namespace) -> int:
    """Resolve all [[graph:...]] references in SPEC.md and print the result."""
    from factory.discovery.spec import resolve_spec
    from factory.spec import read_spec
    from factory.spec.resolver import resolve_references

    project_path = Path(args.path).resolve()
    if not project_path.is_dir():
        print(f"Error: not a directory: {project_path}", file=sys.stderr)
        return 1

    spec_path = resolve_spec(project_path)
    if spec_path is None:
        print("Error: no repo spec found (run 'factory spec generate' first)", file=sys.stderr)
        return 1

    spec_content = read_spec(project_path)
    resolved = resolve_references(spec_content, project_path)
    print(resolved)
    return 0
