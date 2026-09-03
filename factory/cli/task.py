"""CLI commands for task management: create, validate, list."""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from pathlib import Path


def add_task_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Add 'task' subcommand group to the CLI parser."""
    task_parser = sub.add_parser("task", help="Task management (create, validate, list)")
    task_sub = task_parser.add_subparsers(dest="task_command")

    # factory task list
    p_list = task_sub.add_parser("list", help="List all discovered tasks")
    p_list.add_argument(
        "--project", "-p", default=None, help="Project directory for task discovery"
    )

    # factory task validate
    p_validate = task_sub.add_parser("validate", help="Validate a task definition")
    p_validate.add_argument("name", help="Task name to validate")
    p_validate.add_argument(
        "--mode", default=None, help="Check compatibility with a specific mode"
    )
    p_validate.add_argument(
        "--project", "-p", default=None, help="Project directory"
    )

    # factory task create
    p_create = task_sub.add_parser("create", help="Scaffold a new task definition")
    p_create.add_argument("source", help="Repository URL or local path")
    p_create.add_argument(
        "--project", "-p", default=".", help="Project directory to write task to"
    )


def cmd_task(args: argparse.Namespace) -> int:
    """Dispatch task subcommands."""
    task_cmd = getattr(args, "task_command", None)
    if not task_cmd:
        print("Usage: factory task {list,validate,create}", file=sys.stderr)
        return 1

    handlers = {
        "list": _cmd_task_list,
        "validate": _cmd_task_validate,
        "create": _cmd_task_create,
    }

    handler = handlers.get(task_cmd)
    if handler is None:
        print(f"Unknown task command: {task_cmd}", file=sys.stderr)
        return 1

    return handler(args)


def _cmd_task_list(args: argparse.Namespace) -> int:
    """List all discovered tasks in a table."""
    from factory.task_registry import TaskRegistry

    project = Path(args.project).resolve() if args.project else None
    TaskRegistry.reset()
    entries = TaskRegistry.list_tasks(project)

    if not entries:
        print("No tasks found.")
        return 0

    # Table header
    print(f"{'Source':<10} {'Name':<20} {'Scoring':<14} {'Instances':<14} {'Description'}")
    print(f"{'─' * 10} {'─' * 20} {'─' * 14} {'─' * 14} {'─' * 40}")

    for entry in entries:
        print(
            f"{entry.source:<10} {entry.name:<20} {entry.scoring:<14} "
            f"{entry.instances_format:<14} {entry.description[:40]}"
        )

    return 0


def _cmd_task_validate(args: argparse.Namespace) -> int:
    """Validate a task definition."""
    from factory.task_registry import TaskRegistry

    project = Path(args.project).resolve() if getattr(args, "project", None) else None
    TaskRegistry.reset()

    name = args.name
    checks: list[tuple[str, bool, str]] = []

    try:
        task = TaskRegistry.load_task(name, project)
        checks.append(("Identity", True, f"Valid name '{task.name}'"))
    except (KeyError, ValueError) as exc:
        checks.append(("Identity", False, str(exc)))
        _print_checks(name, checks)
        return 1

    defn = task.definition

    # Description
    checks.append((
        "Description",
        bool(defn.description),
        defn.description[:60] if defn.description else "Missing description",
    ))

    # instances()
    try:
        insts = list(task.instances())
        checks.append((
            "instances()",
            len(insts) > 0,
            f"Format '{defn.instances_config.format}', {len(insts)} instance(s)",
        ))
    except Exception as exc:
        checks.append(("instances()", False, str(exc)))

    # setup()
    has_setup = bool(defn.setup_config.command)
    checks.append((
        "setup()",
        True,
        f"Command: '{defn.setup_config.command}'" if has_setup else "No-op (default)",
    ))

    # prompt()
    try:
        inst = insts[0] if insts else None
        if inst:
            p = task.prompt(inst)
            checks.append(("prompt()", bool(p), p[:60]))
        else:
            checks.append(("prompt()", False, "No instances to test"))
    except Exception as exc:
        checks.append(("prompt()", False, str(exc)))

    # verify()
    has_verify = bool(defn.verify_config.command)
    checks.append((
        "verify()",
        has_verify,
        f"Command: '{defn.verify_config.command}'" if has_verify else "No verify command",
    ))

    # Scoring
    scoring_method = getattr(defn.scoring, "method", "unknown")
    checks.append(("Scoring", True, f"{type(defn.scoring).__name__} (method={scoring_method})"))

    # Constraints
    checks.append((
        "Constraints",
        defn.constraints.timeout >= 60,
        f"Timeout {defn.constraints.timeout}s, max_retries={defn.constraints.max_retries}",
    ))

    # Mode compatibility
    mode = getattr(args, "mode", None)
    if mode:
        try:
            from factory.compose import validate_composition
            from factory.workflow.registry import WorkflowRegistry

            workflow = WorkflowRegistry.get_workflow(mode)
            if workflow is None:
                checks.append(("Mode-compat", False, f"Mode '{mode}' not found"))
            else:
                validate_composition(workflow, task)
                checks.append(("Mode-compat", True, f"Compatible with '{mode}'"))
        except Exception as exc:
            checks.append(("Mode-compat", False, str(exc)))
    else:
        checks.append(("Mode-compat", True, "(skipped — use --mode to check)"))

    _print_checks(name, checks)
    all_passed = all(ok for _, ok, _ in checks)
    return 0 if all_passed else 1


def _cmd_task_create(args: argparse.Namespace) -> int:
    """Scaffold a new task definition from a repository."""
    source = args.source
    project = Path(args.project).resolve()

    task_dir = project / ".factory" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)

    # Derive name from source — strip URL artifacts first
    cleaned = urllib.parse.urlparse(source).path.rstrip("/")
    name = Path(cleaned).stem.replace("_", "-").replace(" ", "-").lower()
    if name.endswith(".git"):
        name = name[:-4]

    toml_path = task_dir / f"{name}.toml"

    content = f"""[task]
name = "{name}"
description = "TODO: describe this task"

[instances]
format = "directory"
source = "instances/"

[setup]
command = "pip install -e {{instance_dir}}"

[prompt]
text = "Implement the feature. All tests must pass."

[verify]
command = "pytest -xvs"

[scoring]
method = "pytest"
partial_credit = true

[constraints]
timeout = 3600
max_retries = 1
"""
    toml_path.write_text(content)
    print(f"Generated: {toml_path}")
    print(f"  Name: {name}")
    print("  ⚠ TODO: Review and customize the generated task definition.")
    print(f"\nRun 'factory task validate {name}' to verify the definition.")
    return 0


def _print_checks(name: str, checks: list[tuple[str, bool, str]]) -> None:
    """Print check results in a readable format."""
    print(f"\nRunning checks on '{name}'...")
    for label, passed, detail in checks:
        mark = "✓" if passed else "✗"
        print(f"  {mark} {label + ':':<15} {detail}")
    all_passed = all(ok for _, ok, _ in checks)
    print(f"\nResult: {'All checks passed ✓' if all_passed else 'Some checks failed ✗'}")
