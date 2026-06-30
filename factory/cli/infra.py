"""CLI infra commands."""
from __future__ import annotations

import argparse
import json
import structlog
import sys
from datetime import datetime
from pathlib import Path

from factory.cli._helpers import _emit_cli_event, _print_banner, _run

log = structlog.get_logger()

def cmd_archive(args: argparse.Namespace) -> int:
    from factory.obsidian.notes import (
        update_memory_index,
        write_experiment_note,
        write_project_dashboard,
        write_strategy_note,
    )
    from factory.state import detect_state
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    records = _run(store.load_history())

    if not records:
        print("Nothing to archive.")
        return 0

    project_name = project_path.name
    state = detect_state(project_path).value

    # Write experiment notes
    for record in records:
        write_experiment_note(project_name, record)

    # Build eval_dimensions list for dashboard
    eval_dimensions: list[dict] | None = None
    profile = _run(store.read_eval_profile())
    if profile:
        eval_dimensions = [d.model_dump() for d in profile.dimensions]

    # Current score from latest experiment
    scores = [r.score_after for r in records if r.score_after is not None]
    current_score = scores[-1] if scores else None

    write_project_dashboard(project_name, state, current_score, records, eval_dimensions)

    # Write strategy note if strategy exists
    strategy_text = _run(store.read_strategy())
    if strategy_text:
        write_strategy_note(project_name, strategy_text)

    # Update MEMORY.md index
    update_memory_index()

    from factory.obsidian.notes import vault_path as get_vault_path

    vp = get_vault_path()
    _emit_cli_event(project_path, "archive.completed", {
        "experiments": len(records),
        "vault": str(vp) if vp else "none",
    })
    if vp:
        print(f"Archived {len(records)} experiments to {vp}")
    else:
        print(f"Archived {len(records)} experiments (vault not configured, skipped vault writes)")
    return 0


def cmd_checkpoint(args: argparse.Namespace) -> int:
    """Show or save a checkpoint for crash-resilient resume."""
    from factory.checkpoint import (
        CheckpointState,
        clear_checkpoint,
        format_checkpoint,
        load_checkpoint,
        save_checkpoint,
    )

    project_path = Path(args.path).resolve()

    if args.clear:
        clear_checkpoint(project_path)
        print("Checkpoint cleared.")
        return 0

    if args.save:
        completed_hyps: list[int] = []
        if args.completed_hypotheses:
            completed_hyps = [int(x.strip()) for x in args.completed_hypotheses.split(",") if x.strip()]
        state = CheckpointState(
            mode=args.mode or "improve",
            active_experiment_id=args.experiment,
            completed_agents=[a.strip() for a in args.completed.split(",")] if args.completed else [],
            pending_agents=[a.strip() for a in args.pending.split(",")] if args.pending else [],
            last_eval_scores=json.loads(args.scores) if args.scores else {},
            current_hypothesis=args.hypothesis,
            completed_hypotheses=completed_hyps,
            timestamp=datetime.now().isoformat(),
        )
        save_checkpoint(project_path, state)
        print(f"Checkpoint saved to {project_path / '.factory' / 'checkpoint.json'}")
        return 0

    # Show current checkpoint
    loaded = load_checkpoint(project_path)
    if loaded is None:
        print("No checkpoint found.")
        return 0
    print(format_checkpoint(loaded))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    """Load checkpoint and display resume context for the CEO."""
    from factory.checkpoint import format_checkpoint, load_checkpoint

    project_path = Path(args.path).resolve()
    state = load_checkpoint(project_path)
    if state is None:
        print("No checkpoint found. Nothing to resume.")
        return 1

    print("=== Resume Context ===")
    print(format_checkpoint(state))
    print()
    print("The CEO should resume from this state, skipping completed agents")
    print(f"and continuing with: {', '.join(state.pending_agents) or 'none'}")
    return 0


def cmd_backfill_archive(args: argparse.Namespace) -> int:
    """Generate archive notes for experiments missing from .factory/archive/experiments/."""
    from factory.backfill_archive import backfill_archive

    project_path = Path(args.path).resolve()
    result = _run(backfill_archive(project_path))
    print(
        f"Archive backfill complete: {result['existed']} existed, "
        f"{result['created']} created, {result['total']} total"
    )
    return 0


def cmd_vault_init(args: argparse.Namespace) -> int:
    from factory.obsidian.notes import init_vault

    vault_result = init_vault()
    if vault_result is None:
        print("No vault path configured. Set FACTORY_VAULT_PATH or run:")
        print("  export FACTORY_VAULT_PATH=~/factory-vault")
        print("  factory vault-init")
        return 1
    print(f"Factory vault initialized at {vault_result}")
    return 0


def cmd_serve_mcp(args: argparse.Namespace) -> int:
    """Start the Factory MCP stdio server."""
    from factory.mcp_server import main as mcp_main

    mcp_main()
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Launch the Factory live dashboard server."""
    from factory.dashboard.app import create_app

    projects_dir = Path(args.projects_dir).expanduser().resolve()
    port = args.port
    host = args.host

    _print_banner("dashboard")
    print(f"  Dashboard: http://{host}:{port}", file=sys.stderr)
    print(f"  Projects:  {projects_dir}\n", file=sys.stderr)

    app = create_app(projects_dir)

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0

