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
    from factory.spec.validate import validate_spec

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
        result = _run(validate_spec(project_path))
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        _emit_cli_event(project_path, "spec.validate.failed", {"error": str(exc)[:200]})
        return 1

    output_path = project_path / ".factory" / "spec_validation.md"
    _emit_cli_event(
        project_path,
        "spec.validate.completed",
        {
            "errors": len(result.errors),
            "warnings": len(result.warnings),
            "output": str(output_path),
        },
    )

    if result.errors:
        print(f"FAIL: {len(result.errors)} error(s), {len(result.warnings)} warning(s)")
        for err in result.errors:
            print(f"  ERROR: {err}")
        for warn in result.warnings:
            print(f"  WARN: {warn}")
    else:
        print(f"PASS: {len(result.warnings)} warning(s)")
        for warn in result.warnings:
            print(f"  WARN: {warn}")

    print(f"Report: {output_path}")
    return 0 if result.passed else 1


def cmd_spec_scope(args: argparse.Namespace) -> int:
    """Scope a diff against the existing repo spec."""
    from factory.discovery.spec import resolve_spec
    from factory.spec.update import scope_diff

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
        scope = _run(scope_diff(project_path, experiment_id=exp_id))
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        _emit_cli_event(project_path, "spec.scope.failed", {"error": str(exc)[:200]})
        return 1

    _emit_cli_event(
        project_path,
        "spec.scope.completed",
        {
            "affected_modules": len(scope.affected_modules),
            "new_files": len(scope.new_files),
            "deleted_files": len(scope.deleted_files),
        },
    )

    output_path = project_path / ".factory" / "spec_update_scope.md"
    print(
        f"Scope: {len(scope.affected_modules)} affected modules, "
        f"{len(scope.new_files)} new files, {len(scope.deleted_files)} deleted"
    )
    print(f"Report: {output_path}")
    return 0


def cmd_spec_update(args: argparse.Namespace) -> int:
    """Update a repo spec based on changes since last spec commit."""
    from factory.discovery.spec import resolve_spec
    from factory.spec.update import update_spec

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
    from factory.spec.impact import get_impact

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
