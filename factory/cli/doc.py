"""CLI doc-drift command — detect documentation drift from recently merged PRs."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import structlog

log = structlog.get_logger()


def cmd_doc_drift(args: argparse.Namespace) -> int:
    """Run the doc-drift workflow to detect stale documentation."""
    from factory.workflow.executor import WorkflowExecutor
    from factory.workflow.definitions import register_all

    project_path = Path(args.path).resolve()
    if not project_path.is_dir():
        print(f"Error: {project_path} is not a directory", file=sys.stderr)
        return 1

    workflows = register_all()
    wf = workflows.get("doc-drift")
    if wf is None:
        print("Error: doc-drift workflow not found", file=sys.stderr)
        return 1

    log.info("doc_drift.start", project=str(project_path), days=args.days, dry_run=args.dry_run)

    executor = WorkflowExecutor(
        workflow=wf,
        project_path=project_path,
        dry_run=args.dry_run,
    )

    result = asyncio.run(executor.execute())
    if result.success:
        log.info("doc_drift.complete", nodes_run=result.nodes_executed)
    else:
        log.warning("doc_drift.halted", reason=result.halt_reason)

    return 0 if result.success else 1
