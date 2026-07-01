"""CLI registry commands."""
from __future__ import annotations

import argparse
import structlog
from pathlib import Path

from factory.cli._helpers import _emit_cli_event

log = structlog.get_logger()

def cmd_report_update(args: argparse.Namespace) -> int:
    """Generate a performance report for a project."""
    from factory.report import save_performance_report

    project_path = Path(args.path).resolve()
    report_path = save_performance_report(project_path)
    print(f"Performance report written to {report_path}")
    return 0


def cmd_registry_list(args: argparse.Namespace) -> int:
    """List all registered factory-managed projects."""
    from factory.registry import list_projects

    projects = list_projects()
    if not projects:
        print("No registered projects. Projects are auto-registered when experiments begin.")
        return 0

    header = f"{'Name':<30} {'Experiments':>11} {'Score':>8} {'Last Experiment':<20}"
    print(header)
    print("-" * len(header))
    for p in projects:
        score = f"{p.latest_score:.3f}" if p.latest_score is not None else "n/a"
        last = p.last_experiment_at.strftime("%Y-%m-%d %H:%M") if p.last_experiment_at else "never"
        print(f"{p.name:<30} {p.experiment_count:>11} {score:>8} {last:<20}")
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    from factory.digest import format_digest, scan_vault

    target_date = None
    if args.date:
        from datetime import date as date_cls
        target_date = date_cls.fromisoformat(args.date)

    projects = scan_vault(target_date=target_date, days=args.days)
    output = format_digest(projects, target_date=target_date, days=args.days)
    print(output)
    return 0


def cmd_insights(args: argparse.Namespace) -> int:
    from factory.insights import (
        analyze,
        discover_projects,
        format_insights,
        load_all_histories,
    )

    project_path = Path(args.path).resolve()
    projects_dir_raw = getattr(args, "projects_dir", None)
    if projects_dir_raw:
        projects_dir = Path(projects_dir_raw).expanduser().resolve()
    else:
        from factory.registry import get_project_paths
        reg_paths = get_project_paths()
        if reg_paths:
            projects_dir = reg_paths[0].parent
        else:
            projects_dir = project_path.parent
    _emit_cli_event(project_path, "insights.started", {"projects_dir": str(projects_dir)})
    project_paths = discover_projects(projects_dir)

    if not project_paths:
        print("No factory-managed projects found.")
        return 0

    histories = load_all_histories(project_paths)
    if not histories:
        print("No experiment histories found.")
        return 0

    insights = analyze(histories)
    report = format_insights(insights)

    # Write to .factory/strategy/insights.md
    out_path = project_path / ".factory" / "strategy" / "insights.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)

    _emit_cli_event(project_path, "insights.completed", {
        "projects_analyzed": len(project_paths),
        "total_experiments": sum(len(h) for h in histories.values()),
    })
    print(report)
    print(f"\nWritten to {out_path}")
    return 0

