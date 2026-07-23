"""CLI subcommands for the workflow graph engine."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import structlog

from factory.workflow.registry import WorkflowRegistry
from factory.workflow.executor import WorkflowExecutor
from factory.workflow.primitives import (
    DEFAULT_AGENT_POOL,
    AgentNode,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    Study,
)

log = structlog.get_logger()


def cmd_workflow(args: argparse.Namespace) -> int:
    """Dispatch workflow subcommands."""
    sub = getattr(args, "workflow_command", None)
    if not sub:
        print("Usage: factory workflow {run,list,show,validate,export-skills,lint-contributed}")
        return 1

    handlers = {
        "run": _cmd_run,
        "list": _cmd_list,
        "show": _cmd_show,
        "validate": _cmd_validate,
        "export-skills": _cmd_export_skills,
        "lint-contributed": _cmd_lint_contributed,
    }

    handler = handlers.get(sub)
    if handler:
        return handler(args)

    print(f"Unknown workflow subcommand: {sub}")
    return 1


def _cmd_run(args: argparse.Namespace) -> int:
    """Run a named workflow on a project."""
    name = args.name
    project_path = Path(args.project_path).resolve()
    dry_run = getattr(args, "dry_run", False)

    wf = WorkflowRegistry.get_workflow(name, project_path)
    if not wf:
        print(f"Unknown workflow: {name}")
        print(f"Available: {', '.join(WorkflowRegistry._entries)}")
        return 1

    executor = WorkflowExecutor(
        wf,
        project_path,
        agent_pool=DEFAULT_AGENT_POOL,
        dry_run=dry_run,
    )

    from factory.agents.runner import begin_cycle_session, complete_cycle_session
    cycle_span_id = begin_cycle_session(project_path, cycle_id=name)

    try:
        result = asyncio.run(executor.execute())

        print(json.dumps({
            "workflow": name,
            "success": result.success,
            "halted": result.halted,
            "halt_reason": result.halt_reason,
            "nodes_executed": result.nodes_executed,
            "duration_ms": round(result.duration_ms, 1),
            "files_produced": sorted(result.completed_files),
        }, indent=2))

        return 0 if result.success else 1
    finally:
        complete_cycle_session(project_path, cycle_span_id)


def _cmd_list(args: argparse.Namespace) -> int:
    """List all registered workflows."""
    project_path = Path(getattr(args, "project_path", None) or ".").resolve()
    entries = WorkflowRegistry.list_workflows(project_path)

    header = f"{'Name':<12} {'Nodes':>6} {'Edges':>6} {'Start Node':<20}"
    print(header)
    print("-" * len(header))

    for entry in entries:
        wf = WorkflowRegistry.get_workflow(entry.name)
        if wf:
            print(f"{entry.name:<12} {len(wf.nodes):>6} {len(wf.edges):>6} {wf.start_node:<20}")

    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    """Show a workflow's graph as a node/edge table."""
    name = args.name
    project_path = Path(getattr(args, "project_path", None) or ".").resolve()
    wf = WorkflowRegistry.get_workflow(name, project_path)
    if not wf:
        print(f"Unknown workflow: {name}")
        return 1

    print(f"Workflow: {wf.name}")
    print(f"Start:    {wf.start_node}")
    print()

    # Nodes table
    print("Nodes:")
    header = f"  {'ID':<25} {'Type':<12} {'Blocking':>8} {'Reads':<30} {'Writes':<30}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for nid, node in wf.nodes.items():
        ntype = type(node).__name__
        blocking = "yes" if node.blocking else "async"
        reads = ", ".join(sorted(node.reads)) if node.reads else "-"
        writes = ", ".join(sorted(node.writes)) if node.writes else "-"

        if isinstance(node, AgentNode):
            ntype = f"Agent({node.role.value})"
        elif isinstance(node, GateNode):
            ntype = f"Gate({node.evaluator_type})"
        elif isinstance(node, ForkNode):
            ntype = f"Fork({len(node.targets)})"
        elif isinstance(node, JoinNode):
            ntype = f"Join({len(node.sources)})"
        elif isinstance(node, Study):
            ntype = "Study"
        elif isinstance(node, FnNode):
            ntype = "Fn"

        if len(reads) > 28:
            reads = reads[:25] + "..."
        if len(writes) > 28:
            writes = writes[:25] + "..."

        print(f"  {nid:<25} {ntype:<12} {blocking:>8} {reads:<30} {writes:<30}")

    print()

    # Edges table
    print("Edges:")
    header = f"  {'Source':<25} {'Target':<25} {'Condition':<15}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for edge in wf.edges:
        cond = edge.condition.value if edge.condition else "-"
        print(f"  {edge.source:<25} {edge.target:<25} {cond:<15}")

    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """Validate a workflow using NetworkX."""
    name = args.name
    project_path = Path(getattr(args, "project_path", None) or ".").resolve()
    wf = WorkflowRegistry.get_workflow(name, project_path)
    if not wf:
        print(f"Unknown workflow: {name}")
        return 1

    issues = wf.validate_graph()

    if not issues:
        print(f"Workflow '{name}': VALID ({len(wf.nodes)} nodes, {len(wf.edges)} edges)")
        return 0

    print(f"Workflow '{name}': {len(issues)} issue(s) found:")
    for issue in issues:
        print(f"  - {issue}")
    return 1


def _cmd_export_skills(args: argparse.Namespace) -> int:
    """Export workflow definitions as Claude Code SKILL.md files."""
    from factory.workflow.skill_export import export_all_skills, validate_skill

    output_dir = Path(getattr(args, "output_dir", None) or ".").resolve()
    verify = getattr(args, "verify", False)

    project_path = Path(getattr(args, "project_path", None) or ".").resolve()
    entries = WorkflowRegistry.discover(project_path)
    workflows = {}
    for name, entry in entries.items():
        wf = WorkflowRegistry.get_workflow(name)
        if wf:
            workflows[name] = wf
    generated = export_all_skills(output_dir, workflows)

    print(f"Exported {len(generated)} skills to {output_dir}/")
    for path in generated:
        print(f"  {path.relative_to(output_dir)}")

    if verify:
        total_issues = 0
        for path in generated:
            content = path.read_text()
            issues = validate_skill(content)
            if issues:
                print(f"\n  INVALID: {path.name}")
                for issue in issues:
                    print(f"    - {issue}")
                total_issues += len(issues)

        if total_issues:
            print(f"\n{total_issues} validation issue(s) found.")
            return 1
        print("\nAll skills valid.")

    return 0


def _cmd_lint_contributed(args: argparse.Namespace) -> int:
    """Lint contributed workflow directories for required artifacts and structure."""
    from factory.workflow.lint import lint_contributed

    base_dir = Path(getattr(args, "path", None) or
                    Path(__file__).resolve().parent / "contributed")

    issues = lint_contributed(base_dir)

    if not issues:
        print(f"All contributed workflows in {base_dir} are clean.")
        return 0

    for issue in issues:
        print(f"{issue.directory}: [{issue.check}] {issue.message}")
    print(f"\n{len(issues)} issue(s) found.")
    return 1


def add_workflow_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the 'workflow' subcommand with its subcommands."""
    wf_parser = sub.add_parser("workflow", help="Workflow graph engine commands")
    wf_sub = wf_parser.add_subparsers(dest="workflow_command")

    # run
    p = wf_sub.add_parser("run", help="Run a named workflow on a project")
    p.add_argument("name", help="Workflow name (build, design, improve, research, meta)")
    p.add_argument("project_path", help="Path to the project")
    p.add_argument("--dry-run", action="store_true", help="Execute without real agent calls")

    # list
    p = wf_sub.add_parser("list", help="List all registered workflows")
    p.add_argument("--project-path", default=None, help="Project path for local workflow discovery")

    # show
    p = wf_sub.add_parser("show", help="Show workflow graph details")
    p.add_argument("name", help="Workflow name")
    p.add_argument("--project-path", default=None, help="Project path for local workflow discovery")

    # validate
    p = wf_sub.add_parser("validate", help="Validate workflow graph structure")
    p.add_argument("name", help="Workflow name")
    p.add_argument("--project-path", default=None, help="Project path for local workflow discovery")

    # export-skills
    p = wf_sub.add_parser("export-skills", help="Export workflows as SKILL.md files")
    p.add_argument(
        "--output-dir", default=".", help="Output directory (default: current directory)"
    )
    p.add_argument("--verify", action="store_true", help="Validate generated skills")
    p.add_argument("--project-path", default=None, help="Project path for local workflow discovery")

    # lint-contributed
    p = wf_sub.add_parser("lint-contributed", help="Lint contributed workflow directories")
    p.add_argument(
        "--path", default=None, help="Base directory to scan (default: factory/workflow/contributed/)"
    )
