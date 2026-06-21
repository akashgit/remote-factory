"""CLI subcommands for the workflow graph engine."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import structlog

from factory.workflow.definitions import register_all
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
        print("Usage: factory workflow {run,list,show,validate}")
        return 1

    handlers = {
        "run": _cmd_run,
        "list": _cmd_list,
        "show": _cmd_show,
        "validate": _cmd_validate,
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

    workflows = register_all()
    wf = workflows.get(name)
    if not wf:
        print(f"Unknown workflow: {name}")
        print(f"Available: {', '.join(workflows)}")
        return 1

    executor = WorkflowExecutor(
        wf,
        project_path,
        agent_pool=DEFAULT_AGENT_POOL,
        dry_run=dry_run,
    )

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


def _cmd_list(args: argparse.Namespace) -> int:
    """List all registered workflows."""
    workflows = register_all()

    header = f"{'Name':<12} {'Nodes':>6} {'Edges':>6} {'Start Node':<20}"
    print(header)
    print("-" * len(header))

    for name, wf in workflows.items():
        print(f"{name:<12} {len(wf.nodes):>6} {len(wf.edges):>6} {wf.start_node:<20}")

    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    """Show a workflow's graph as a node/edge table."""
    name = args.name
    workflows = register_all()
    wf = workflows.get(name)
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
    workflows = register_all()
    wf = workflows.get(name)
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
    wf_sub.add_parser("list", help="List all registered workflows")

    # show
    p = wf_sub.add_parser("show", help="Show workflow graph details")
    p.add_argument("name", help="Workflow name")

    # validate
    p = wf_sub.add_parser("validate", help="Validate workflow graph structure")
    p.add_argument("name", help="Workflow name")
