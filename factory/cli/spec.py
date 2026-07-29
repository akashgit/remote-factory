"""Spec subcommands — generate, validate, scope, update, impact."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from factory.cli._helpers import _emit_cli_event, _run


def _run_spec_workflow(name: str, project_path: Path) -> tuple[int, str]:
    """Run a spec workflow (spec-generate or spec-update) through the gated executor.

    Returns (exit_code, error_reason). error_reason is empty on success.
    """
    import asyncio

    from factory.workflow.definitions import spec_generate_workflow, spec_update_workflow
    from factory.workflow.executor import WorkflowExecutor
    from factory.workflow.primitives import DEFAULT_AGENT_POOL

    wf = spec_generate_workflow() if name == "spec-generate" else spec_update_workflow()
    executor = WorkflowExecutor(wf, project_path, agent_pool=DEFAULT_AGENT_POOL)
    result = asyncio.run(executor.execute())

    if not result.success:
        reason = result.halt_reason or "unknown error"
        print(f"Error: {name} workflow failed: {reason}", file=sys.stderr)
        return 1, reason
    return 0, ""


def cmd_spec_generate(args: argparse.Namespace) -> int:
    """Generate a repo spec for a project."""
    project_path = Path(args.path).resolve()
    if not project_path.is_dir():
        print(f"Error: not a directory: {project_path}", file=sys.stderr)
        return 1

    _emit_cli_event(project_path, "spec.generate.started", {"path": str(project_path)})
    rc, reason = _run_spec_workflow("spec-generate", project_path)
    if rc != 0:
        _emit_cli_event(project_path, "spec.generate.failed", {"error": reason[:200]})
        return rc

    spec_path = project_path / "SPEC.md"
    _emit_cli_event(project_path, "spec.generate.completed", {"output": str(spec_path)})
    print(f"Repo spec generated: {spec_path}")
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

    project_path = Path(args.path).resolve()
    if not project_path.is_dir():
        print(f"Error: not a directory: {project_path}", file=sys.stderr)
        return 1

    spec_path = resolve_spec(project_path)
    if spec_path is None:
        print("Error: no repo spec found (run 'factory spec generate' first)", file=sys.stderr)
        return 1

    _emit_cli_event(project_path, "spec.update.started", {"path": str(project_path)})
    rc, reason = _run_spec_workflow("spec-update", project_path)
    if rc != 0:
        _emit_cli_event(project_path, "spec.update.failed", {"error": reason[:200]})
        return rc

    _emit_cli_event(project_path, "spec.update.completed", {"output": str(spec_path)})
    print(f"Repo spec updated: {spec_path}")
    return 0


def cmd_spec_apply_diff(args: argparse.Namespace) -> int:
    """Apply a SPEC Diff from strategy to SPEC.md."""
    from factory.spec.apply_diff import apply_spec_diff

    project_path = Path(args.path).resolve()
    if not project_path.is_dir():
        print(f"Error: not a directory: {project_path}", file=sys.stderr)
        return 1

    _emit_cli_event(project_path, "spec.apply_diff.started", {"path": str(project_path)})

    strategy_path = None
    if hasattr(args, "strategy") and args.strategy:
        strategy_path = Path(args.strategy).resolve()

    applied = apply_spec_diff(project_path, strategy_path=strategy_path)

    if applied:
        _emit_cli_event(project_path, "spec.apply_diff.completed", {"applied": True})
        print("SPEC Diff applied to SPEC.md")
    else:
        _emit_cli_event(project_path, "spec.apply_diff.completed", {"applied": False})
        print("No SPEC Diff to apply (skipped)")

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
