"""CLI entry point — argparse subcommands wrapping library functions."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.messages import Message


def _run(coro):  # noqa: ANN001, ANN202
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ── banner ────────────────────────────────────────────────────


_DASHBOARD_PORT = 8420


def _dashboard_is_running(port: int = _DASHBOARD_PORT) -> bool:
    """Check if the dashboard is already listening on the given port."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _ensure_dashboard(project_path: Path, port: int = _DASHBOARD_PORT) -> None:
    """Start the dashboard in the background if it's not already running.

    Prints the dashboard URL to stderr either way.
    """
    url = f"http://localhost:{port}"

    if _dashboard_is_running(port):
        print(f"  Dashboard: {url} (running)", file=sys.stderr)
        return

    # Determine projects directory (parent of the project)
    projects_dir = project_path.parent

    # Start dashboard as a detached background process
    cmd = [
        sys.executable, "-m", "factory", "dashboard",
        "--projects-dir", str(projects_dir),
        "--port", str(port),
        "--host", "0.0.0.0",
    ]
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # detach from parent process
    )
    print(f"  Dashboard: {url} (started)", file=sys.stderr)


def _print_banner(mode: str = "improve") -> None:
    """Print the Factory startup banner to stderr."""
    if os.environ.get("NO_COLOR") or not sys.stderr.isatty():
        print(f"Factory v2 — mode: {mode}", file=sys.stderr)
        return

    c = "\033[1;36m"  # bold cyan
    d = "\033[2m"      # dim
    r = "\033[0m"      # reset

    banner = (
        f"\n{c}  ┏━╸┏━┓┏━╸╺┳╸┏━┓┏━┓╻ ╻{r}\n"
        f"{c}  ┣╸ ┣━┫┃   ┃ ┃ ┃┣┳┛┗┳┛{r}\n"
        f"{c}  ╹  ╹ ╹┗━╸ ╹ ┗━┛╹┗╸ ╹ {r}\n"
        f"{d}  Multi-Agent Software Evolution{r}\n"
        f"{d}  Mode: {mode}{r}\n"
    )
    print(banner, file=sys.stderr)


# ── subcommand handlers ────────────────────────────────────────


def cmd_home(args: argparse.Namespace) -> int:
    """Print the factory installation root directory."""
    factory_home = Path(__file__).resolve().parent.parent
    print(factory_home)
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    from factory.state import detect_state

    project_path = Path(args.path)
    state = detect_state(project_path)
    _emit_cli_event(project_path, "detect", {"state": state.value})
    print(state.value)
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    from factory.discovery.generate import write_eval_script
    from factory.discovery.introspect import introspect_project
    from factory.discovery.profile import build_eval_profile
    from factory.store import ExperimentStore

    project_path = Path(args.path)
    _emit_cli_event(project_path, "discover.started", {"path": str(project_path)})

    profile = introspect_project(project_path)
    eval_profile = build_eval_profile(profile)

    # Persist artifacts so detect_state can find them
    store = ExperimentStore(project_path)
    store.factory_dir.mkdir(exist_ok=True)
    _run(store.save_eval_profile(eval_profile))
    write_eval_script(eval_profile, project_path)

    dims = [d.name for d in eval_profile.dimensions]
    _emit_cli_event(project_path, "discover.completed", {
        "language": profile.language,
        "framework": profile.framework,
        "dimensions": dims,
    })

    output = {
        "project": profile.model_dump(),
        "eval_profile": eval_profile.model_dump(),
    }
    print(json.dumps(output, indent=2))

    if profile.discovered_evals:
        print("\nDiscovered project eval scripts:", file=sys.stderr)
        for e in profile.discovered_evals:
            print(f"  - {e.name}: {e.command}", file=sys.stderr)
        print(
            "\nTo use these as project-specific eval dimensions, add them to "
            "factory.md under ## Project Eval:",
            file=sys.stderr,
        )
        for e in profile.discovered_evals:
            print(f"  - name: {e.name}", file=sys.stderr)
            print(f"    command: {e.command}", file=sys.stderr)
            print("    parse: json", file=sys.stderr)

    return 0


def cmd_init(args: argparse.Namespace) -> int:
    from factory.store import ExperimentStore

    project_path = Path(args.path)
    store = ExperimentStore(project_path)

    factory_md = project_path / "factory.md"
    if not factory_md.exists():
        print("Error: factory.md not found. Create it first or use --reparse.", file=sys.stderr)
        return 1

    # Ensure .factory/ dir exists so reparse_config can write config.json
    store.factory_dir.mkdir(exist_ok=True)
    config = _run(store.reparse_config())

    if args.reparse:
        print(f"Reparsed config: goal={config.goal!r}")
    else:
        _run(store.init(config))
        print(f"Initialized .factory/ — goal={config.goal!r}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from factory.eval.runner import run_eval
    from factory.store import ExperimentStore

    project_path = Path(args.path)
    store = ExperimentStore(project_path)
    config = _run(store.read_config())
    skip_project_eval = getattr(args, "skip_project_eval", False)
    _emit_cli_event(project_path, "eval.started", {"command": config.eval_command})
    score = _run(run_eval(
        config.eval_command, project_path, config.eval_threshold,
        project_eval=config.project_eval or None,
        eval_weights=config.eval_weights,
        skip_project_eval=skip_project_eval,
    ))
    _emit_cli_event(project_path, "eval.completed", {
        "composite": score.total,
        "passed": score.passed,
        "dimensions": len(score.results),
    })
    print(json.dumps(score.model_dump(), indent=2, default=str))
    return 0 if score.passed else 1


def cmd_guard(args: argparse.Namespace) -> int:
    from factory.eval.guards import check_all

    project_path = Path(args.path)

    # Optionally load scope and fixed surfaces from factory config
    scope = None
    fixed_surfaces = None
    if args.check_scope or args.check_surfaces:
        from factory.store import ExperimentStore
        store = ExperimentStore(project_path)
        config = _run(store.read_config())
        if args.check_scope:
            scope = config.scope
        if args.check_surfaces:
            fixed_surfaces = config.fixed_surfaces

    violations = check_all(
        project_path, args.baseline, allowed_scope=scope, fixed_surfaces=fixed_surfaces,
    )
    _emit_cli_event(project_path, "guard.completed", {
        "violations": len(violations),
        "clean": len(violations) == 0,
    })
    if violations:
        for v in violations:
            print(f"VIOLATION: {v}")
        return 1
    print("clean")
    return 0


def cmd_begin(args: argparse.Namespace) -> int:
    from factory.store import ExperimentStore

    project_path = Path(args.path)
    store = ExperimentStore(project_path)
    exp_id = _run(store.begin(args.hypothesis))
    _emit_cli_event(project_path, "experiment.begin", {
        "exp_id": exp_id,
        "hypothesis": args.hypothesis[:200],
    })
    print(exp_id)
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    from factory.precheck import run_precheck
    from factory.store import ExperimentStore
    from factory.models import ExperimentRecord, FactoryConfig

    project_path = Path(args.path)
    store = ExperimentStore(project_path)
    score_before = getattr(args, "score_before", None)
    score_after = getattr(args, "score_after", None)
    verdict = args.verdict
    notes = args.notes or ""

    force = getattr(args, "force", False)

    if verdict == "keep" and not force:
        config_path = project_path / ".factory" / "config.json"
        if config_path.exists():
            config = FactoryConfig(**json.loads(config_path.read_text()))
            history = _run(store.load_history())
            history_dicts = [r.model_dump() for r in history]

            precheck_result = run_precheck(
                score_before=score_before,
                score_after=score_after,
                threshold=config.eval_threshold,
                hypothesis=args.hypothesis or "",
                history=history_dicts,
                project_path=project_path,
                smoke_test_command=config.smoke_test,
                hard_constraints=config.hard_constraints,
            )

            if not precheck_result.passed:
                verdict = "revert"
                failure_detail = "; ".join(precheck_result.blocking_failures)
                notes = f"[OVERRIDDEN by finalize gate] precheck failed: {failure_detail}. {notes}"
                _emit_cli_event(project_path, "verdict.overridden", {
                    "exp_id": args.id,
                    "original_verdict": "keep",
                    "new_verdict": "revert",
                    "reason": failure_detail,
                })
                print(f"Finalize gate: precheck FAILED — overriding keep to revert ({failure_detail})")

    if verdict == "keep" and force:
        _emit_cli_event(project_path, "verdict.force_kept", {
            "exp_id": args.id,
        })
        print("Finalize gate: precheck SKIPPED (--force)")

    record = ExperimentRecord(
        id=args.id,
        timestamp=datetime.now(),
        hypothesis=args.hypothesis or "",
        change_summary=args.summary or "",
        issue_number=args.issue,
        pr_number=args.pr,
        score_before=score_before,
        score_after=score_after,
        delta=None,
        verdict=verdict,
        cost_usd=args.cost,
        notes=notes,
    )
    _run(store.finalize(args.id, record))
    _emit_cli_event(project_path, "experiment.finalize", {
        "exp_id": args.id,
        "verdict": verdict,
        "hypothesis": (args.hypothesis or "")[:200],
    })
    print(f"Finalized experiment {args.id} — verdict={verdict}")
    return 0


def cmd_message(args: argparse.Namespace) -> int:
    """Queue a message for the CEO agent."""
    from factory.messages import write_message

    project_path = Path(args.path)
    if not project_path.exists():
        print(f"Error: project path does not exist: {project_path}", file=sys.stderr)
        return 1
    if not (project_path / ".factory").exists():
        print(f"Error: not a factory project (no .factory/ directory): {project_path}", file=sys.stderr)
        return 1
    if not args.text or not args.text.strip():
        print("Error: message text must not be empty.", file=sys.stderr)
        return 1
    try:
        msg = write_message(project_path, args.text)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Message queued (id={msg.id}). The CEO will see it at the start of the next cycle.")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    from factory.store import ExperimentStore

    store = ExperimentStore(Path(args.path))
    records = _run(store.load_history())
    if not records:
        print("No experiments recorded.")
        return 0

    header = f"{'ID':>4}  {'Verdict':>7}  {'Delta':>8}  {'Cost':>8}  Hypothesis"
    print(header)
    print("-" * len(header))
    for r in records:
        delta = f"{r.delta:+.4f}" if r.delta is not None else "    n/a"
        cost = f"${r.cost_usd:.2f}" if r.cost_usd is not None else "     n/a"
        hyp = r.hypothesis[:60]
        print(f"{r.id:>4}  {r.verdict:>7}  {delta:>8}  {cost:>8}  {hyp}")
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    from factory.notify.telegram import TelegramNotifier
    from factory.store import ExperimentStore

    project_path = Path(args.path)
    store = ExperimentStore(project_path)
    records = _run(store.load_history())
    notifier = TelegramNotifier()
    _run(notifier.send_digest(project_path.name, records, None))
    print("Digest sent.")
    return 0


def cmd_study(args: argparse.Namespace) -> int:
    from factory.study import study_project

    project_path = Path(args.path)
    _emit_cli_event(project_path, "study.started", {})
    kwargs: dict[str, object] = {}
    projects_dir = getattr(args, "projects_dir", None)
    if projects_dir:
        kwargs["projects_dir"] = str(Path(projects_dir).expanduser().resolve())
    focus = getattr(args, "focus", None)
    summary = study_project(project_path, focus=focus, **kwargs)

    # Write to .factory/strategy/observations.md
    obs_path = project_path / ".factory" / "strategy" / "observations.md"
    obs_path.parent.mkdir(parents=True, exist_ok=True)
    obs_path.write_text(summary)

    _emit_cli_event(project_path, "study.completed", {"chars": len(summary)})
    print(summary)
    return 0


def cmd_backlog_remove(args: argparse.Namespace) -> int:
    from factory.study import remove_backlog_item

    project_path = Path(args.path)
    item_text = args.item
    if remove_backlog_item(project_path, item_text):
        print(f"Removed backlog item: {item_text}")
        return 0
    print(f"Backlog item not found: {item_text}", file=sys.stderr)
    return 1


def cmd_backlog_list(args: argparse.Namespace) -> int:
    from factory.study import _migrate_legacy_backlog, _parse_backlog_items, _persist_backlog_items

    project_path = Path(args.path)
    _migrate_legacy_backlog(project_path)
    items = _parse_backlog_items(project_path)
    if not items:
        print("No backlog items.")
        return 0
    _persist_backlog_items(project_path, items)
    for item in items:
        print(f"- {item}")
    return 0


def cmd_backlog_add(args: argparse.Namespace) -> int:
    from factory.study import add_backlog_item

    project_path = Path(args.path)
    item_text = args.item
    if add_backlog_item(project_path, item_text):
        print(f"Added backlog item: {item_text}")
        return 0
    print(f"Backlog item already exists: {item_text}", file=sys.stderr)
    return 1


def cmd_status(args: argparse.Namespace) -> int:
    from factory.state import detect_state
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    state = detect_state(project_path)
    print(f"Project: {project_path}")
    print(f"State: {state.value}")

    if state.value == "has_factory":
        store = ExperimentStore(project_path)
        try:
            config = _run(store.read_config())
        except FileNotFoundError:
            config = None

        # Try to read latest eval score
        profile = _run(store.read_eval_profile())
        if profile:
            dims = ", ".join(d.name for d in profile.dimensions)
            print(f"Eval dimensions: {dims}")

        records = _run(store.load_history())
        if records:
            kept = sum(1 for r in records if r.verdict == "keep")
            reverted = sum(1 for r in records if r.verdict == "revert")
            total = len(records)
            print(f"Experiments: {total} total ({kept} kept, {reverted} reverted)")
            last = records[-1]
            print(f'Last experiment: #{last.id} — "{last.hypothesis}" ({last.verdict})')
            scores = [r.score_after for r in records if r.score_after is not None]
            if scores:
                print(f"Latest score: {scores[-1]:.3f}")
        else:
            print("Experiments: none")

        if config:
            print(f"Goal: {config.goal}")

    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    """Generate an end-of-session summary report."""
    from factory.summary import format_summary, generate_summary, save_summary

    project_path = Path(args.path).resolve()
    _emit_cli_event(project_path, "summary.started", {})
    summary = _run(generate_summary(project_path))
    output = format_summary(summary)
    _run(save_summary(project_path, summary))
    _emit_cli_event(project_path, "summary.completed", {
        "kept": len(summary.experiments_kept),
        "reverted": len(summary.experiments_reverted),
        "errored": len(summary.experiments_errored),
        "backlog": len(summary.backlog_remaining),
    })
    print(output)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export a complete project snapshot as JSON to stdout."""
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    factory_dir = project_path / ".factory"

    if not factory_dir.is_dir():
        print(f"Error: {factory_dir} does not exist. Run 'factory init' first.", file=sys.stderr)
        return 1

    store = ExperimentStore(project_path)

    # Read config
    try:
        config = _run(store.read_config())
        config_data = config.model_dump()
    except FileNotFoundError:
        config_data = None

    # Read eval profile
    eval_profile = _run(store.read_eval_profile())
    eval_profile_data = eval_profile.model_dump() if eval_profile else None

    # Read experiment history
    records = _run(store.load_history())
    experiments_data = [r.model_dump() for r in records]

    # Read strategy
    strategy = _run(store.read_strategy())

    # Assemble snapshot
    snapshot = {
        "config": config_data,
        "eval_profile": eval_profile_data,
        "experiments": experiments_data,
        "strategy": strategy,
        "meta": {
            "project_path": str(project_path),
            "timestamp": datetime.now().isoformat(),
            "factory_version": "0.1.0",
        },
    }

    json.dump(snapshot, sys.stdout, indent=2, default=str)
    print()  # trailing newline
    return 0


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


def cmd_ace(args: argparse.Namespace) -> int:
    """Run ACE self-improvement on agent playbooks."""
    from factory.ace.curator import curate_playbook
    from factory.ace.models import Playbook
    from factory.ace.paths import seed_user_playbooks, user_playbook_path, user_playbooks_dir
    from factory.ace.reflector import reflect_on_experiments, update_counters_from_experiments
    from factory.insights import discover_projects, load_all_histories

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
    dry_run = getattr(args, "dry_run", False)

    _emit_cli_event(project_path, "ace.started", {"dry_run": dry_run})

    # Step 0: Update counters on existing playbooks from experiment verdicts
    user_dir = user_playbooks_dir()
    if not dry_run:
        seed_user_playbooks()
        project_paths = discover_projects(projects_dir)
        if project_path not in project_paths:
            project_paths.append(project_path)
        histories = load_all_histories(project_paths)
        all_records = [r for records in histories.values() for r in records]
        if all_records:
            update_counters_from_experiments(user_dir, all_records)

    # Step 1: Reflect — analyze experiment data, generate candidate bullets
    candidates = reflect_on_experiments(projects_dir, project_path)

    if not candidates:
        print("No candidate playbook bullets generated (not enough experiment data).")
        return 0

    # Step 2: Curate — merge with existing playbooks, prune
    roles_updated = []
    for role, items in candidates.items():
        playbook_path = user_playbook_path(role)
        if playbook_path.exists():
            existing = Playbook.from_markdown(playbook_path.read_text())
        else:
            existing = Playbook.empty(role)

        updated = curate_playbook(existing, items)

        if dry_run:
            print(f"\n{'=' * 60}")
            print(f"DRY RUN — {role} ({len(items)} candidates → {len(updated.items)} items)")
            print(f"{'=' * 60}")
            print(updated.to_markdown())
        else:
            playbook_path.write_text(updated.to_markdown())
            print(f"  {role}: {len(updated.items)} items → {playbook_path}")
            roles_updated.append(role)

    _emit_cli_event(project_path, "ace.completed", {
        "roles_updated": roles_updated,
        "candidates": len(candidates),
        "dry_run": dry_run,
    })

    if not dry_run:
        print(f"\nPlaybooks updated in {user_dir}")

    return 0


def cmd_ace_stats(args: argparse.Namespace) -> int:
    """Print a table of all playbook items with their helpful/harmful/net counters."""
    from factory.ace.models import Playbook
    from factory.ace.paths import DEFAULTS_DIR, user_playbooks_dir

    user_dir = user_playbooks_dir()

    all_items: list[tuple[str, str, int, int, int, str]] = []
    seen_roles: set[str] = set()

    # User-local playbooks take priority
    for playbook_path in sorted(user_dir.glob("*.md")):
        role = playbook_path.stem
        seen_roles.add(role)
        playbook = Playbook.from_markdown(playbook_path.read_text())
        for item in playbook.items:
            all_items.append((
                role,
                item.id,
                item.helpful,
                item.harmful,
                item.net_score,
                item.content[:60],
            ))

    # Fall back to defaults for roles without user-local
    for playbook_path in sorted(DEFAULTS_DIR.glob("*.md")):
        role = playbook_path.stem
        if role in seen_roles:
            continue
        playbook = Playbook.from_markdown(playbook_path.read_text())
        for item in playbook.items:
            all_items.append((
                role,
                item.id,
                item.helpful,
                item.harmful,
                item.net_score,
                item.content[:60],
            ))

    if not all_items:
        print("No playbook items found.")
        return 0

    # Print table header
    header = f"{'Role':<12} {'ID':<14} {'helpful':>7} {'harmful':>7} {'net':>5}  Text"
    print(header)
    print("-" * len(header))

    total_helpful = 0
    total_harmful = 0
    for role, item_id, helpful, harmful, net, text in all_items:
        print(f"{role:<12} {item_id:<14} {helpful:>7} {harmful:>7} {net:>5}  {text}")
        total_helpful += helpful
        total_harmful += harmful

    print("-" * len(header))
    print(
        f"Total: {len(all_items)} bullets, "
        f"helpful={total_helpful}, harmful={total_harmful}, "
        f"net={total_helpful - total_harmful}"
    )
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


def cmd_archive(args: argparse.Namespace) -> int:
    from factory.obsidian.notes import (
        update_memory_index,
        write_experiment_note,
        write_project_dashboard,
        write_strategy_note,
    )
    from factory.state import detect_state
    from factory.store import ExperimentStore

    project_path = Path(args.path)
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


def cmd_precheck(args: argparse.Namespace) -> int:
    """Run hard precheck gate before keep/revert decision."""
    from factory.precheck import run_precheck
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    config = _run(store.read_config())

    # Load history as dicts for anti-pattern matching
    records = _run(store.load_history())
    history = [
        {
            "id": r.id,
            "hypothesis": r.hypothesis,
            "verdict": r.verdict,
            "delta": r.delta,
        }
        for r in records
    ]

    result = run_precheck(
        score_before=args.score_before,
        score_after=args.score_after,
        threshold=config.eval_threshold,
        hypothesis=args.hypothesis or "",
        history=history,
        project_path=project_path,
        baseline_sha=args.baseline,
        allowed_scope=config.scope if args.baseline else None,
        smoke_test_command=config.smoke_test,
        similarity_threshold=args.similarity_threshold,
        fixed_surfaces=config.fixed_surfaces if config.fixed_surfaces else None,
    )

    # Output as JSON for machine consumption
    output = {
        "passed": result.passed,
        "checks": [
            {"name": c.name, "passed": c.passed, "detail": c.detail}
            for c in result.checks
        ],
        "blocking_failures": result.blocking_failures,
    }
    print(json.dumps(output, indent=2))

    _emit_cli_event(project_path, "precheck.completed", {
        "passed": result.passed,
        "failures": result.blocking_failures,
    })

    return 0 if result.passed else 1


def cmd_leakage_check(args: argparse.Namespace) -> int:
    """Check text for ground truth leakage against fixed surface fingerprints."""
    from factory.research.leakage import fingerprint_fixed_surfaces, scan_for_leakage
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    config = _run(store.read_config())

    if not config.fixed_surfaces:
        print("SKIP: no fixed_surfaces configured in factory.md")
        return 0

    fingerprints = fingerprint_fixed_surfaces(project_path, config.fixed_surfaces)
    if not fingerprints:
        print("SKIP: no fixed surface files found to fingerprint")
        return 0

    text = args.text
    if args.text_file:
        text_path = Path(args.text_file)
        if not text_path.is_file():
            print(f"ERROR: text file not found: {args.text_file}")
            return 1
        text = text_path.read_text()
    elif args.text is None:
        import sys
        if not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            print("ERROR: provide --text, --text-file, or pipe to stdin")
            return 1

    report = scan_for_leakage(text, fingerprints, args.sensitivity)

    output = {
        "flagged": report.flagged,
        "risk_level": report.risk_level,
        "findings": [
            {
                "source_file": f.source_file,
                "leaked_token": f.leaked_token,
                "context": f.context,
                "leak_type": f.leak_type,
            }
            for f in report.findings
        ],
    }
    print(json.dumps(output, indent=2))
    return 1 if report.risk_level in ("medium", "high") else 0


def cmd_validate_research(args: argparse.Namespace) -> int:
    """Validate research mode configuration for ground truth isolation."""
    from factory.research.leakage import validate_research_config
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    config = _run(store.read_config())

    errors = validate_research_config(config, project_path)

    if not errors:
        print("VALID: research config passes all ground truth isolation checks")
        return 0

    for error in errors:
        print(f"ERROR: {error}")
    return 1


def cmd_review(args: argparse.Namespace) -> int:
    """Format and optionally post a review on a GitHub PR."""
    from factory.review import ReviewPayload, format_review, post_review

    guard_results: dict[str, str] = {}
    if args.guards:
        for pair in args.guards.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                guard_results[k.strip()] = v.strip()

    payload = ReviewPayload(
        verdict=args.verdict.upper(),
        reason=args.reason or "",
        score_before=args.score_before,
        score_after=args.score_after,
        threshold=args.threshold,
        guard_results=guard_results,
        precheck_summary=args.precheck_summary or "",
        code_notes=[n.strip() for n in args.code_notes.split("|")] if args.code_notes else [],
        experiment_id=args.experiment_id,
        hypothesis=args.hypothesis or "",
    )

    review_body = format_review(payload)

    if args.pr and not args.dry_run:
        success = post_review(args.pr, review_body, payload.verdict, repo=args.repo)
        if success:
            print(f"Review posted on PR #{args.pr}")
        else:
            print(f"Failed to post review on PR #{args.pr}", file=sys.stderr)
            print(review_body)
            return 1
    else:
        print(review_body)

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


def cmd_log(args: argparse.Namespace) -> int:
    """Append a structured event to .factory/events.jsonl."""
    import json as json_mod

    from factory.events import emit_event

    project_path = Path(args.path).resolve()
    event_type = args.event_type

    if args.data:
        try:
            data = json_mod.loads(args.data)
        except json_mod.JSONDecodeError as exc:
            print(f"Error: invalid JSON in --data: {exc}", file=sys.stderr)
            return 1
    else:
        data = {}

    emit_event(project_path, event_type, agent=args.agent, data=data)
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


def cmd_research(args: argparse.Namespace) -> int:
    """Print citation index table and coverage summary."""
    from factory.research_index import build_citation_index, citation_coverage
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    records = _run(store.load_history())

    if not records:
        print("No experiments recorded.")
        return 0

    index = build_citation_index(project_path)
    coverage = citation_coverage(project_path)

    # Print table
    header = f"{'ID':>4}  {'Hypothesis':<52}  Citations"
    print(header)
    print("-" * len(header))
    for r in records:
        hyp = r.hypothesis[:50]
        cites = index.get(r.id, [])
        cite_str = ", ".join(cites) if cites else "-"
        print(f"{r.id:>4}  {hyp:<52}  {cite_str}")

    # Summary
    cited_count = sum(1 for r in records if r.research_citations)
    print()
    print(f"{len(records)} experiments, {cited_count} cited, coverage {coverage:.0%}")
    return 0


def cmd_backfill_citations(args: argparse.Namespace) -> int:
    """Backfill citations from experiment text into .factory/citations.json."""
    from factory.research_index import backfill_citations

    project_path = Path(args.path).resolve()
    index = backfill_citations(project_path)
    print(f"Backfilled citations for {len(index)} experiments")
    for exp_id, cites in sorted(index.items(), key=lambda x: int(x[0])):
        print(f"  #{exp_id}: {', '.join(cites[:5])}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    """Compare two experiments side-by-side."""
    from factory.analysis import compare_experiments, format_comparison
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    comparison = compare_experiments(store, args.id_a, args.id_b)
    print(format_comparison(comparison))
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    """Explain a single experiment with FEEC category and dimension breakdown."""
    from factory.analysis import explain_experiment, format_explanation
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    explanation = explain_experiment(store, args.id)
    print(format_explanation(explanation))
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """Manage ~/.factory/config.toml."""
    sub = getattr(args, "config_command", None)
    if not sub:
        print("Usage: factory config {show,edit,migrate}")
        return 1

    if sub == "show":
        from factory.user_config import show_config

        reveal = getattr(args, "reveal", False)
        print(show_config(reveal=reveal))
        return 0

    if sub == "edit":
        from factory.user_config import CONFIG_PATH, ensure_config_file

        ensure_config_file()
        editor = os.environ.get("EDITOR", "vi")
        return subprocess.call([editor, str(CONFIG_PATH)])

    if sub == "migrate":
        from factory.user_config import migrate_env_to_config

        try:
            msg = migrate_env_to_config()
            print(msg)
            return 0
        except (ImportError, FileExistsError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    print(f"Unknown config subcommand: {sub}", file=sys.stderr)
    return 1


def cmd_emit(args: argparse.Namespace) -> int:
    from factory.events import emit_event

    project_path = Path(args.project).resolve()
    data: dict = {}
    if args.data:
        try:
            data = json.loads(args.data)
        except json.JSONDecodeError as e:
            print(f"Error: --data is not valid JSON: {e}", file=sys.stderr)
            return 1
    emit_event(project_path, args.event_type, agent=args.agent, data=data)
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


def cmd_self_update(args: argparse.Namespace) -> int:
    """Self-update the factory CLI via uv tool upgrade."""
    from importlib.metadata import version as pkg_version

    try:
        version_before = pkg_version("remote-factory")
    except Exception:
        version_before = "unknown"

    print(f"Current version: {version_before}")
    print("Upgrading remote-factory...")

    result = subprocess.run(
        ["uv", "tool", "upgrade", "remote-factory"],
        capture_output=True,
        text=True,
    )

    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)

    if result.returncode != 0:
        print("Upgrade failed.", file=sys.stderr)
        return 1

    # Re-check version (may not reflect in this process, but show what uv reported)
    try:
        version_after = pkg_version("remote-factory")
    except Exception:
        version_after = "unknown"

    print(f"Version after upgrade: {version_after}")
    if version_before == version_after:
        print("Already up to date.")
    else:
        print(f"Updated: {version_before} -> {version_after}")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    """Install Factory agents as Claude Code agents."""
    from factory.agents.plugin import generate_agent_content, load_agent_config

    agents_dir = Path.home() / ".claude" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    role_filter = getattr(args, "role", None)
    config = load_agent_config()

    if role_filter and role_filter not in config:
        print(f"Unknown role: {role_filter!r}", file=sys.stderr)
        print(f"Available roles: {', '.join(config)}", file=sys.stderr)
        return 1

    roles = [role_filter] if role_filter else list(config)

    for role in roles:
        content = generate_agent_content(role)
        agent_path = agents_dir / f"factory-{role}.md"
        agent_path.write_text(content)
        print(f"  Installed factory-{role} -> {agent_path}")

    print()
    print("Usage:")
    print("  claude --agent factory-<role>              # from any project directory")
    print('  claude --agent factory-ceo "improve X"     # with initial prompt')
    print()
    print("Or from within Claude Code, ask: \"use the factory-<role> agent\"")
    return 0


def cmd_agent(args: argparse.Namespace) -> int:
    """Invoke a specialist agent with the given task."""
    from factory.agents.runner import invoke_agent
    from factory.user_config import load_config

    profile = getattr(args, "profile", None)
    load_config(profile=profile)

    role = args.role
    task = args.task
    project_path = Path(args.project).resolve()
    timeout = getattr(args, "timeout", 600.0)
    model = _resolve_model(args)
    runner = _resolve_runner(args)

    result, code = _run(invoke_agent(
        role,
        task,
        project_path,
        timeout=timeout,
        dangerously_skip_permissions=True,
        model=model,
        runner_name=runner,
    ))
    print(result)
    return code


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


def cmd_ceo(args: argparse.Namespace) -> int:
    """Launch the Factory CEO agent to orchestrate a project.

    Default: interactive foreground session (user can see and interact).
    With --headless: pipe mode via claude -p (for scripting, cron, etc.).
    With --mode interactive: brainstorm an idea via research + Distiller before building.
    """
    from factory.agents.runner import resolve_prompt
    from factory.runners import get_runner
    from factory.user_config import load_config

    profile = getattr(args, "profile", None)
    load_config(profile=profile)

    raw_path = getattr(args, "path", None)
    mode = getattr(args, "mode", "auto")
    headless = getattr(args, "headless", False)
    prompt_file = getattr(args, "prompt", None)
    focus = getattr(args, "focus", None)
    dir_name = getattr(args, "dir", None)

    if not raw_path:
        print("Error: provide a project path, GitHub URL, idea file, or prompt",
              file=sys.stderr)
        return 1

    no_github = getattr(args, "no_github", False)

    if mode == "interactive":
        if headless:
            print("Error: --mode interactive requires foreground mode "
                  "(incompatible with --headless)", file=sys.stderr)
            return 1
        if prompt_file:
            print("Error: --mode interactive and --prompt are mutually exclusive. "
                  "Interactive mode generates the spec; --prompt provides one.",
                  file=sys.stderr)
            return 1
        if focus:
            print("Error: --mode interactive and --focus are mutually exclusive. "
                  "Interactive mode is for new ideas; --focus targets existing "
                  "backlog items.", file=sys.stderr)
            return 1

    if mode == "research":
        if prompt_file:
            print("Error: --mode research and --prompt are mutually exclusive. "
                  "Research ideation generates the spec; --prompt provides one.",
                  file=sys.stderr)
            return 1

    interactive_idea: str | None = None
    research_ideation: str | None = None
    if mode == "interactive":
        # In interactive mode the positional arg is always an idea string, not a path.
        # Skip _resolve_input to avoid misinterpreting the idea as a file/directory.
        interactive_idea = raw_path
        slug = _slugify(dir_name) if dir_name else _extract_project_name(raw_path)
        project_path = _dedupe_project_path(_get_projects_dir() / slug, raw_path)
        _ensure_repo(project_path)
        context = None
    elif mode == "research" and not (resolved := Path(raw_path).expanduser()).is_dir() and not resolved.is_file():
        # New research project from idea — enter research ideation
        if headless:
            print("Error: --mode research for new projects requires foreground mode "
                  "(incompatible with --headless)", file=sys.stderr)
            return 1
        if focus:
            print("Error: --focus cannot be used with research ideation for new projects. "
                  "--focus targets existing backlog items.", file=sys.stderr)
            return 1
        research_ideation = raw_path
        slug = _slugify(dir_name) if dir_name else _extract_project_name(raw_path)
        project_path = _dedupe_project_path(_get_projects_dir() / slug, raw_path)
        _ensure_repo(project_path)
        context = None
    else:
        project_path, context = _resolve_input(raw_path, dir_name=dir_name)
    if prompt_file:
        context = _read_prompt_file(project_path, prompt_file)
    issue_number: int | None = None
    issue_url: str | None = None
    if focus:
        from factory.issue import is_issue_ref
        if is_issue_ref(focus) and no_github:
            print("Error: --focus resolved to an issue reference, but --no-github is set. "
                  "Issue fetching requires GitHub/GitLab CLI access.", file=sys.stderr)
            return 1
        issue_resolved = _resolve_focus_issue(focus, project_path)
        if issue_resolved:
            title, context, issue_number, issue_url = issue_resolved
            focus = f"{title} (issue #{issue_number})"
    force_fresh = mode == "auto-fresh"
    if mode in ("auto", "auto-fresh"):
        mode = _auto_detect_mode(
            project_path, has_prompt=bool(prompt_file or context),
            force_fresh=force_fresh,
        )
    discover_only = getattr(args, "discover_only", False)
    min_growth = getattr(args, "min_growth", None)
    max_new = getattr(args, "max_new", None)
    branch = getattr(args, "branch", None)
    model = _resolve_model(args)
    runner_name = _resolve_runner(args)

    if mode == "research" and not research_ideation and not _has_research_target(project_path):
        print("Error: --mode research requires research_target in factory.md. "
              "Either configure research_target manually, or pass an idea string "
              "to start research ideation: factory ceo \"your idea\" --mode research",
              file=sys.stderr)
        return 1

    if focus and prompt_file:
        print("Error: --focus (targeted mode) and --prompt are mutually exclusive. "
              "--focus builds one backlog item; --prompt executes a spec file.", file=sys.stderr)
        return 1
    if focus and mode not in ("improve", "research"):
        print(f"Error: --focus (targeted mode) only works in improve or research mode, got '{mode}'. "
              "The project must already be built before targeting specific items.", file=sys.stderr)
        return 1

    _print_banner("ideation" if mode in ("interactive", "research") and (interactive_idea or research_ideation) else mode)
    _ensure_dashboard(project_path)

    if focus:
        from factory.study import add_backlog_item
        add_backlog_item(project_path, focus)

    from factory.messages import mark_read, read_pending

    pending = read_pending(project_path)
    pending_ids = [m.id for m in pending]

    ceo_mode = "build" if mode == "interactive" or research_ideation else mode
    task = _build_ceo_task(
        project_path, ceo_mode, context, focus=focus, prompt_file=prompt_file,
        min_growth=min_growth, max_new=max_new, branch=branch,
        discover_only=discover_only, no_github=no_github,
        interactive_idea=interactive_idea,
        research_ideation=research_ideation,
        messages=pending,
        issue_number=issue_number,
        issue_url=issue_url,
    )

    if headless:
        # Non-interactive pipe mode (for scripting, cron, tmux)
        # Uses completion guard to auto-resume on premature exit
        from factory.ceo_completion import run_ceo_with_completion_guard

        result, code = _run(run_ceo_with_completion_guard(
            project_path,
            task,
            mode=mode,
            runner_name=runner_name,
            model=model,
            timeout=7200.0,
        ))
        print(result)
        if code == 0:
            if pending_ids:
                mark_read(project_path, pending_ids)
        if code != 0:
            return code
        return _chain_modes(
            project_path, focus=focus,
            min_growth=min_growth, max_new=max_new, branch=branch,
            already_improved=mode in ("improve", "meta") or discover_only,
            model=model, no_github=no_github,
        )

    # Interactive foreground mode: use runner's interactive_exec.
    # Mark read before exec — interactive_exec replaces the process via os.execvp
    # so there's no post-execution hook. If the session fails to launch, messages
    # are lost. This is accepted: the user is at the terminal and can re-send.
    if pending_ids:
        print(
            f"Consuming {len(pending_ids)} message(s): {', '.join(pending_ids)}",
            file=sys.stderr,
        )
        mark_read(project_path, pending_ids)
    prompt = resolve_prompt("ceo", project_path)
    runner = get_runner(runner_name)
    runner.interactive_exec(
        prompt, task, project_path,
        model=model, role="ceo", dangerously_skip_permissions=True
    )


def _is_github_url(path: str) -> bool:
    """Return True if path looks like a GitHub URL."""
    return path.startswith("https://github.com/") or path.startswith("git@github.com:")


# ── universal input resolver ─────────────────────────────────


def _resolve_model(args: argparse.Namespace) -> str | None:
    """Resolve model: CLI flag > FACTORY_MODEL env var > config.toml > None."""
    from factory.user_config import resolve

    flag = (getattr(args, "model", None) or "").strip() or None
    return resolve("model", cli_value=flag, env_var="FACTORY_MODEL")


def _resolve_runner(args: argparse.Namespace) -> str | None:
    """Resolve runner: CLI flag > FACTORY_RUNNER env var > None (default to 'claude').

    Returns None to let get_runner() handle the default.
    """
    flag = (getattr(args, "runner", None) or "").strip()
    if flag:
        return flag
    return None


def _get_projects_dir() -> Path:
    from factory.user_config import resolve

    raw = resolve("projects_dir", env_var="FACTORY_PROJECTS_DIR", default=str(Path.home() / "factory-projects"))
    return Path(raw).expanduser() if raw else Path.home() / "factory-projects"


def _resolve_input(raw: str, dir_name: str | None = None) -> tuple[Path, str | None]:
    """Resolve any user input to (project_path, optional_context).

    Handles four input types in priority order:
    1. Existing directory → use directly
    2. Existing file → read as spec, create repo
    3. GitHub URL → clone
    4. Raw prompt → create repo, use prompt as spec
    """
    # 1. Existing directory
    expanded = Path(raw).expanduser()
    if expanded.is_dir():
        return expanded.resolve(), None

    # 2. Existing file (e.g. path to an idea/spec .md file)
    if expanded.is_file():
        idea_content = expanded.read_text()
        slug = _slugify(dir_name) if dir_name else _slugify(expanded.stem.split("\u2014")[0].strip())
        project_path = _dedupe_project_path(_get_projects_dir() / slug, idea_content)
        _ensure_repo(project_path)
        _persist_spec(project_path, idea_content)
        print(f"Idea file: {expanded.name}")
        print(f"Project directory: {project_path}")
        return project_path, idea_content

    # 3. GitHub URL
    if _is_github_url(raw):
        tmp_dir = tempfile.mkdtemp(prefix="factory-")
        subprocess.run(["git", "clone", raw, tmp_dir], check=True)
        print(f"Cloned {raw} → {tmp_dir}")
        return Path(tmp_dir).resolve(), None

    # 4. Raw prompt
    slug = _slugify(dir_name) if dir_name else _extract_project_name(raw)
    project_path = _dedupe_project_path(_get_projects_dir() / slug, raw)
    _ensure_repo(project_path)
    _persist_spec(project_path, raw)
    print(f"New project from prompt: {project_path}")
    return project_path, raw


_FILLER_WORDS = frozenset({
    "a", "an", "the", "that", "which", "with", "for", "and", "or", "to", "using",
    "comprehensive", "simple", "basic", "advanced", "new", "custom", "full",
    "complete", "modern", "robust", "scalable", "lightweight", "minimal",
    "fully", "featured", "production", "ready",
})

_VERB_RE = re.compile(
    r"^(build|create|make|implement|develop|design|write|add|set\s*up|construct|craft)\b\s*"
)


def _extract_project_name(description: str) -> str:
    """Extract a concise project name from a verbose description.

    Strips leading imperative verbs and filler words, then takes
    up to 4 whitespace-delimited tokens (hyphenated compounds like
    ``real-time`` count as one token).
    """
    text = description.lower().strip()
    text = _VERB_RE.sub("", text)
    words = [w for w in re.split(r"\s+", text) if w and w not in _FILLER_WORDS]
    name = "-".join(words[:4])
    return _slugify(name) if name else _slugify(description[:50])


def _dedupe_project_path(project_path: Path, new_spec: str) -> Path:
    """Append a numeric suffix if the directory already holds a different project."""
    spec_path = project_path / ".factory" / "strategy" / "current.md"
    if not spec_path.exists():
        return project_path
    if new_spec.strip() in spec_path.read_text():
        return project_path
    base = project_path
    counter = 2
    while True:
        candidate = base.parent / f"{base.name}-{counter}"
        cand_spec = candidate / ".factory" / "strategy" / "current.md"
        if not cand_spec.exists():
            return candidate
        if new_spec.strip() in cand_spec.read_text():
            return candidate
        counter += 1


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:50].rstrip("-") or "factory-project"


def _ensure_repo(project_path: Path) -> None:
    """Create directory + git init if needed."""
    project_path.mkdir(parents=True, exist_ok=True)
    if not (project_path / ".git").is_dir():
        subprocess.run(["git", "init"], cwd=project_path, capture_output=True, check=True)


def _read_prompt_file(project_path: Path, prompt_file: str) -> str:
    """Read a prompt file (absolute or relative to project) and persist it as the build spec.

    Always overwrites current.md — the user is explicitly passing a new phase prompt.
    """
    prompt_path = Path(prompt_file)
    if not prompt_path.is_absolute():
        prompt_path = project_path / prompt_path
    if not prompt_path.exists():
        print(f"Error: prompt file not found: {prompt_path}", file=sys.stderr)
        sys.exit(1)
    content = prompt_path.read_text()
    strategy_dir = project_path / ".factory" / "strategy"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    spec_path = strategy_dir / "current.md"
    spec_path.write_text(f"## Project Specification\n\n{content}\n")
    print(f"  Prompt: {prompt_path.name} → .factory/strategy/current.md", file=sys.stderr)
    return content


def _resolve_focus_issue(
    focus: str, project_path: Path,
) -> tuple[str, str, int, str] | None:
    """If *focus* looks like an issue ref, fetch it and return (title, context, number, url).

    Returns ``None`` when *focus* is a plain backlog-item name.
    Callers must check ``--no-github`` *before* calling this function.
    """
    from factory.issue import is_issue_ref

    if not is_issue_ref(focus):
        return None

    from factory.issue import fetch_issue, format_issue_as_spec

    issue_spec = fetch_issue(focus, project_path)
    context = format_issue_as_spec(issue_spec)

    strategy_dir = project_path / ".factory" / "strategy"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "current.md").write_text(
        f"## Project Specification\n\n{context}\n"
    )
    print(
        f"  Issue: #{issue_spec.number} → .factory/strategy/current.md",
        file=sys.stderr,
    )
    return issue_spec.title, context, issue_spec.number, issue_spec.url


def _persist_spec(project_path: Path, spec: str) -> None:
    """Write the project spec to .factory/strategy/current.md so all agents can read it.

    This ensures sub-agents spawned by the CEO have access to the original
    idea/prompt, not just the CEO's task string.
    """
    strategy_dir = project_path / ".factory" / "strategy"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    spec_path = strategy_dir / "current.md"
    if not spec_path.exists():
        spec_path.write_text(f"## Project Specification\n\n{spec}\n")


# ── tmux integration ──────────────────────────────────────────


_TMUX_SESSION_PREFIX = "factory-"


def _tmux_session_name(project_path: Path) -> str:
    """Derive a tmux session name from a project path."""
    return f"{_TMUX_SESSION_PREFIX}{project_path.name}"


def _tmux_available() -> bool:
    """Check if tmux is installed."""
    try:
        subprocess.run(["tmux", "-V"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def cmd_tmux(args: argparse.Namespace) -> int:
    """Launch factory run inside a detached tmux session."""
    if not _tmux_available():
        print("Error: tmux is not installed.", file=sys.stderr)
        return 1

    project_path = Path(args.path).resolve()
    session = args.session or _tmux_session_name(project_path)

    # Check if session already exists
    check = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
    )
    if check.returncode == 0:
        if args.attach:
            print(f"Attaching to existing session: {session}")
            os.execvp("tmux", ["tmux", "attach-session", "-t", session])
        print(f"Session '{session}' already running. Use --attach or:")
        print(f"  tmux attach -t {session}")
        return 0

    # Build the factory run command
    factory_root = Path(__file__).resolve().parent.parent
    run_cmd_parts = [
        f"cd {factory_root}",
        "source .venv/bin/activate",
        # Ensure Vertex AI env vars are set (inherit from current env)
        f"export CLAUDE_CODE_USE_VERTEX={os.environ.get('CLAUDE_CODE_USE_VERTEX', '1')}",
        f"export CLOUD_ML_REGION={os.environ.get('CLOUD_ML_REGION', 'your-region')}",
        f"export ANTHROPIC_VERTEX_PROJECT_ID={os.environ.get('ANTHROPIC_VERTEX_PROJECT_ID', '')}",
        # Ensure gcloud SDK is on PATH
        'export PATH="$HOME/google-cloud-sdk/bin:$HOME/.local/bin:$PATH"',
    ]

    model = _resolve_model(args)
    run_args = f"uv run python -m factory run {project_path}"
    if args.mode:
        run_args += f" --mode {args.mode}"
    if args.loop:
        run_args += " --loop"
    if args.interval:
        run_args += f" --interval {args.interval}"
    if args.max_cycles is not None:
        run_args += f" --max-cycles {args.max_cycles}"
    if model:
        run_args += f" --model {shlex.quote(model)}"
    if getattr(args, "no_github", False):
        run_args += " --no-github"

    run_cmd_parts.append(run_args)
    shell_cmd = " && ".join(run_cmd_parts)

    # Create detached tmux session
    result = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, "-x", "200", "-y", "50", shell_cmd],
    )
    if result.returncode != 0:
        print(f"Error: failed to create tmux session '{session}'", file=sys.stderr)
        return 1

    print(f"Factory launched in tmux session: {session}")
    print(f"  tmux attach -t {session}    # attach")
    print(f"  tmux kill-session -t {session}  # stop")

    if args.attach:
        os.execvp("tmux", ["tmux", "attach-session", "-t", session])

    return 0


def cmd_tmux_ls(args: argparse.Namespace) -> int:
    """List running factory tmux sessions."""
    if not _tmux_available():
        print("Error: tmux is not installed.", file=sys.stderr)
        return 1

    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}\t#{session_created}\t#{session_windows}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("No tmux sessions running.")
        return 0

    factory_sessions = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        name = parts[0]
        if name.startswith(_TMUX_SESSION_PREFIX):
            created = datetime.fromtimestamp(int(parts[1])).strftime("%Y-%m-%d %H:%M") if len(parts) > 1 else "?"
            factory_sessions.append((name, created))

    if not factory_sessions:
        print("No factory sessions running.")
        return 0

    print(f"{'Session':<30} {'Started':<20}")
    print("-" * 50)
    for name, created in factory_sessions:
        print(f"{name:<30} {created:<20}")
    return 0


def cmd_tmux_stop(args: argparse.Namespace) -> int:
    """Stop a factory tmux session."""
    if not _tmux_available():
        print("Error: tmux is not installed.", file=sys.stderr)
        return 1

    if args.session:
        session = args.session
    elif args.path:
        session = _tmux_session_name(Path(args.path).resolve())
    else:
        # Stop all factory sessions
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("No tmux sessions running.")
            return 0

        killed = 0
        for name in result.stdout.strip().splitlines():
            if name.startswith(_TMUX_SESSION_PREFIX):
                subprocess.run(["tmux", "kill-session", "-t", name])
                print(f"Stopped: {name}")
                killed += 1

        if killed == 0:
            print("No factory sessions running.")
        else:
            print(f"Stopped {killed} session(s).")
        return 0

    # Kill specific session
    check = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
    )
    if check.returncode != 0:
        print(f"Session '{session}' not found.")
        return 1

    subprocess.run(["tmux", "kill-session", "-t", session])
    print(f"Stopped: {session}")
    return 0



def _has_research_target(project_path: Path) -> bool:
    """Check if project already has research_target configured."""
    try:
        from factory.store import ExperimentStore
        config = _run(ExperimentStore(project_path).read_config())
        return config.research_target is not None
    except (FileNotFoundError, json.JSONDecodeError, ValueError, KeyError):
        return False


def _auto_detect_mode(project_path: Path, has_prompt: bool = False, force_fresh: bool = False) -> str:
    """Detect the right mode based on project state.

    Checks for an in-flight cycle first — if one exists, returns its mode
    regardless of current project state (prevents mode flip on respawn).

    Args:
        project_path: Path to the project.
        has_prompt: True if a build spec is available.
        force_fresh: If True, ignores in-flight cycle and detects from scratch.

    When a build spec is available (--prompt, idea file, or raw prompt),
    no_factory routes to build (not discover).
    """
    from factory.ceo_completion import read_cycle_state
    from factory.models import ProjectState
    from factory.state import detect_state

    # Layer 2: Check for in-flight cycle (unless forced fresh)
    if not force_fresh:
        cycle_state = read_cycle_state(project_path)
        if cycle_state:
            print(
                f"  In-flight cycle: {cycle_state.cycle_id} → mode: {cycle_state.mode} "
                f"(respawns: {cycle_state.respawns})",
                file=sys.stderr,
            )
            return cycle_state.mode

    state = detect_state(project_path)
    mode_map = {
        ProjectState.NO_REPO: "build",
        ProjectState.REPO_INCOMPLETE: "build",
        ProjectState.NO_FACTORY: "build" if has_prompt else "discover",
        ProjectState.EVALS_PENDING_REVIEW: "discover",
        ProjectState.HAS_FACTORY: "improve",
    }
    mode = mode_map[state]

    if state == ProjectState.HAS_FACTORY and _has_research_target(project_path):
        mode = "research"

    print(f"  State: {state.value} → mode: {mode}", file=sys.stderr)
    return mode


def _build_ceo_task(
    project_path: Path,
    mode: str,
    context: str | None = None,
    focus: str | None = None,
    prompt_file: str | None = None,
    min_growth: int | None = None,
    max_new: int | None = None,
    branch: str | None = None,
    discover_only: bool = False,
    no_github: bool = False,
    interactive_idea: str | None = None,
    research_ideation: str | None = None,
    messages: list[Message] | None = None,
    issue_number: int | None = None,
    issue_url: str | None = None,
) -> str:
    """Build the CEO agent task string from mode and optional context."""
    task = f"Project: {project_path}\nMode: {mode}"

    if messages:
        task += "\n\n## User Messages\n"
        task += "The user has sent the following directives. Treat these as HIGH PRIORITY:\n\n"
        for msg in messages:
            ts = msg.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            task += f"**[{ts}]** {msg.text}\n\n"

    if interactive_idea:
        task += (
            f"\n\n## Interactive Ideation Mode (Phase 0)\n\n"
            f"**Raw idea from user:** {interactive_idea}\n\n"
            f"You are in interactive ideation mode. Before building anything, "
            f"you must refine this idea into a complete spec through research "
            f"and iterative user feedback. Follow the Phase 0: Ideation protocol "
            f"in your system prompt.\n\n"
            f"After the user approves the final spec, persist it to "
            f".factory/strategy/current.md and proceed to Build mode.\n"
        )

    if research_ideation:
        task += (
            f"\n\n## Research Ideation Mode (Phase 0)\n\n"
            f"**Raw idea from user:** {research_ideation}\n\n"
            f"You are in research ideation mode. This is like interactive ideation, "
            f"but the Distiller MUST collect research configuration:\n"
            f"- Research Target (objective, metric, target value, run_command, result_path)\n"
            f"- Mutable Surfaces (files the Builder can modify)\n"
            f"- Fixed Surfaces (ground truth / eval files that must never be touched)\n"
            f"- Research Constraints (additional rules)\n"
            f"- Cost Budget (optional)\n\n"
            f"Follow the Phase 0: Ideation protocol, but tell the Distiller this is a "
            f"research project. After the user approves, persist the spec AND the research "
            f"config to .factory/strategy/current.md, then proceed to Build mode. "
            f"During Review mode (factory.md creation), populate the research sections "
            f"from the approved spec.\n"
        )

    if prompt_file:
        task += (
            f"\n\n## Directive\n\n"
            f"The user has provided a specific prompt file (`{prompt_file}`) as the build spec. "
            f"This is your primary instruction — read it at `.factory/strategy/current.md` and "
            f"execute exactly what it describes. Do not infer or improvise beyond what the prompt asks for."
        )

    if focus:
        task += f"\n\n## Focus Directive (Targeted Mode)\n\nTarget: {focus}\n\n"
        if issue_number:
            issue_label = f"#{issue_number}"
            if issue_url:
                issue_label += f" ({issue_url})"
            task += (
                f"This target is from issue {issue_label}. "
                f"The full issue spec has been written to `.factory/strategy/current.md`. "
                f"Read it for the complete requirements.\n\n"
            )
        task += (
            "Single-item mode. This target has been added to the backlog. "
            "The Strategist must generate exactly ONE hypothesis for this item. "
            "No other hypotheses this cycle — no additional backlog clearing, no new items.\n"
            "After this single experiment completes (keep or revert), skip to final archival. "
            "Do not loop back for more hypotheses.\n"
        )
        if issue_number:
            task += (
                f"\n## Issue Tracking\n\n"
                f"This cycle is working on issue #{issue_number}. "
                f"When finalizing, pass `--issue {issue_number}` to `factory finalize`."
            )

    if branch:
        task += (
            f"\n\n## Branch Override\n\n"
            f"Target branch for all PRs and merges: `{branch}`\n"
            f"The Builder should create experiment branches from `{branch}` and "
            f"target PRs against `{branch}`. After revert, checkout `{branch}` instead of main.\n"
        )

    if any(v is not None for v in (min_growth, max_new)):
        budget_lines = ["\n\n## Budget Override\n"]
        budget_lines.append("The user has overridden the hypothesis budget for this run:")
        if min_growth is not None:
            budget_lines.append(f"- **min_growth:** {min_growth} (guaranteed growth hypotheses)")
        if max_new is not None:
            budget_lines.append(f"- **max_new:** {max_new} (max new items added to backlog per cycle)")
        budget_lines.append("")
        budget_lines.append("Pass these overrides to the Strategist. They take precedence over "
                           "factory.md defaults and study-computed values.")
        task += "\n".join(budget_lines)

    if context:
        task += f"\n\n## Project Specification\n\n{context}"

    if mode == "build":
        task += (
            "\n\nRun Build mode: the project is new or incomplete. Follow the Build mode "
            "pipeline (B0-B6): Research → Strategy → Build phases → E2E verification. "
            "Do NOT skip to Improve mode — the project needs to be built first."
        )
    elif mode == "discover":
        if discover_only:
            task += (
                "\n\nRun Discover mode: introspect the project, auto-detect eval dimensions, "
                "and generate the eval harness. Then complete Review mode to initialize the "
                "factory. Do NOT run the Improve loop."
            )
        else:
            task += (
                "\n\nRun Discover mode: introspect the project, auto-detect eval dimensions, "
                "and generate the eval harness. Then complete Review mode: verify the eval "
                "harness works, mark as reviewed, and initialize the factory. "
                "After initialization, proceed to Improve mode for one experiment cycle."
            )
    elif mode == "meta":
        task += (
            "\n\nRun Meta mode: full self-improvement. First, run the complete Improve loop "
            "on this project (experiments, keep/revert decisions). Then run ACE playbook "
            "evolution for all agent roles using cross-project experiment data."
        )
    elif mode == "research":
        task += (
            "\n\nRun Research mode: the project has a research target defined in factory.md. "
            "Read the research_target from config.json to understand the objective, metric, "
            "target value, and run command. Each cycle: form a hypothesis to improve the "
            "metric, implement the change within mutable_surfaces only (leave fixed_surfaces "
            "untouched), run the research command, compare results against the target, and "
            "make a keep/revert decision. Respect research_constraints and cost_budget."
        )

    if no_github:
        task += (
            "\n\n## GitHub Operations Disabled\n\n"
            "The user has passed --no-github. Do NOT:\n"
            "- Create issues on GitHub\n"
            "- Create or post pull requests\n"
            "- Push to remote repositories\n"
            "- Clone from GitHub URLs\n\n"
            "Work locally only. When a GitHub operation would normally occur, "
            "skip it and note what was skipped in the experiment log."
        )

    return task


def _chain_modes(
    project_path: Path,
    focus: str | None = None,
    min_growth: int | None = None,
    max_new: int | None = None,
    branch: str | None = None,
    already_improved: bool = False,
    max_chains: int = 3,
    model: str | None = None,
    no_github: bool = False,
) -> int:
    """After a cycle completes, re-detect state and chain into the next mode.

    This ensures builds and discoveries flow through the full pipeline
    automatically — Build → Discover → Review → Improve — without manual
    re-invocation. Returns 0 when one Improve cycle completes (or all
    chains are exhausted).
    """
    from factory.models import ProjectState
    from factory.state import detect_state

    for i in range(max_chains):
        state = detect_state(project_path)
        if state == ProjectState.HAS_FACTORY and already_improved:
            return 0
        next_mode = _auto_detect_mode(project_path)
        if next_mode == "improve":
            already_improved = True
        print(
            f"[factory] Chaining: state={state.value} → mode={next_mode} "
            f"(chain {i + 1}/{max_chains})",
            file=sys.stderr,
        )
        code = _run_single_cycle(
            project_path, next_mode, focus=focus,
            min_growth=min_growth, max_new=max_new, branch=branch,
            no_github=no_github, model=model,
        )
        if code != 0:
            return code
    return 0


def _run_single_cycle(
    project_path: Path,
    mode: str,
    context: str | None = None,
    focus: str | None = None,
    prompt_file: str | None = None,
    min_growth: int | None = None,
    max_new: int | None = None,
    branch: str | None = None,
    discover_only: bool = False,
    no_github: bool = False,
    model: str | None = None,
    issue_number: int | None = None,
    issue_url: str | None = None,
) -> int:
    """Execute a single factory run cycle via the CEO agent. Returns 0 on success, 1 on error."""
    from factory.agents.runner import invoke_agent

    if focus:
        from factory.study import add_backlog_item
        add_backlog_item(project_path, focus)

    from factory.messages import mark_read, read_pending

    pending = read_pending(project_path)
    pending_ids = [m.id for m in pending]

    task = _build_ceo_task(
        project_path, mode, context, focus=focus, prompt_file=prompt_file,
        min_growth=min_growth, max_new=max_new, branch=branch,
        discover_only=discover_only, no_github=no_github,
        messages=pending,
        issue_number=issue_number,
        issue_url=issue_url,
    )

    result, code = _run(invoke_agent(
        "ceo",
        task,
        project_path,
        timeout=7200.0,
        dangerously_skip_permissions=True,
        model=model,
    ))

    if code == 0:
        if pending_ids:
            mark_read(project_path, pending_ids)

    print(result)
    return code


def cmd_run(args: argparse.Namespace) -> int:
    """Run factory cycle(s) via the CEO agent. Supports single-shot and heartbeat loop."""
    from factory.user_config import load_config

    profile = getattr(args, "profile", None)
    load_config(profile=profile)

    project_path, context = _resolve_input(args.path)
    prompt_file = getattr(args, "prompt", None)
    loop = getattr(args, "loop", False)
    focus = getattr(args, "focus", None)
    discover_only = getattr(args, "discover_only", False)
    no_github = getattr(args, "no_github", False)
    min_growth = getattr(args, "min_growth", None)
    max_new = getattr(args, "max_new", None)
    branch = getattr(args, "branch", None)
    model = _resolve_model(args)

    if prompt_file:
        context = _read_prompt_file(project_path, prompt_file)
    issue_number: int | None = None
    issue_url: str | None = None
    if focus:
        from factory.issue import is_issue_ref
        if is_issue_ref(focus) and no_github:
            print("Error: --focus resolved to an issue reference, but --no-github is set. "
                  "Issue fetching requires GitHub/GitLab CLI access.", file=sys.stderr)
            return 1
        issue_resolved = _resolve_focus_issue(focus, project_path)
        if issue_resolved:
            title, context, issue_number, issue_url = issue_resolved
            focus = f"{title} (issue #{issue_number})"
    mode = getattr(args, "mode", "auto")
    force_fresh = mode == "auto-fresh"
    if mode in ("auto", "auto-fresh"):
        mode = _auto_detect_mode(
            project_path, has_prompt=bool(prompt_file or context),
            force_fresh=force_fresh,
        )

    if focus and loop:
        print("Error: --focus (targeted mode) and --loop are mutually exclusive. "
              "Targeted mode builds exactly one item and exits.", file=sys.stderr)
        return 1
    if focus and prompt_file:
        print("Error: --focus (targeted mode) and --prompt are mutually exclusive. "
              "--focus builds one backlog item; --prompt executes a spec file.", file=sys.stderr)
        return 1
    if focus and mode not in ("improve", "research"):
        print(f"Error: --focus (targeted mode) only works in improve or research mode, got '{mode}'. "
              "The project must already be built before targeting specific items.", file=sys.stderr)
        return 1

    _print_banner(mode)
    _ensure_dashboard(project_path)

    budget_kwargs = dict(min_growth=min_growth, max_new=max_new, branch=branch)
    skip_improve = mode in ("improve", "meta") or discover_only

    if not loop:
        code = _run_single_cycle(
            project_path, mode, context, focus=focus, prompt_file=prompt_file,
            discover_only=discover_only, no_github=no_github, model=model,
            issue_number=issue_number,
            issue_url=issue_url,
            **budget_kwargs,
        )
        if code != 0:
            return code
        return _chain_modes(
            project_path, focus=focus, already_improved=skip_improve,
            min_growth=min_growth, max_new=max_new, branch=branch,
            model=model, no_github=no_github,
        )

    # Heartbeat loop mode
    interval: int = getattr(args, "interval", 1800)
    max_cycles: int | None = getattr(args, "max_cycles", None)
    shutdown_event = threading.Event()

    def _shutdown_handler(signum: int, frame: object) -> None:
        shutdown_event.set()

    old_sigterm = signal.signal(signal.SIGTERM, _shutdown_handler)
    old_sigint = signal.signal(signal.SIGINT, _shutdown_handler)

    cycle = 0
    start_time = time.monotonic()

    try:
        while True:
            cycle += 1
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[factory] Cycle {cycle} started at {ts}")
            _emit_cli_event(project_path, "cycle.started", {"cycle": cycle, "mode": mode})

            _run_single_cycle(
                project_path, mode, context, focus=focus, prompt_file=prompt_file,
                discover_only=discover_only, no_github=no_github, model=model,
                issue_number=issue_number,
                issue_url=issue_url,
                **budget_kwargs,
            )
            _chain_modes(
                project_path, focus=focus, already_improved=skip_improve,
                min_growth=min_growth, max_new=max_new, branch=branch,
                model=model, no_github=no_github,
            )
            _emit_cli_event(project_path, "cycle.completed", {"cycle": cycle, "mode": mode})

            # Re-detect mode for next cycle (state may have advanced)
            mode = _auto_detect_mode(project_path, has_prompt=bool(prompt_file or context))

            if shutdown_event.is_set():
                break

            if max_cycles is not None and cycle >= max_cycles:
                break

            print(f"[factory] Cycle {cycle} completed. Sleeping for {interval}s...")

            shutdown_event.wait(interval)

            if shutdown_event.is_set():
                break
    finally:
        signal.signal(signal.SIGTERM, old_sigterm)
        signal.signal(signal.SIGINT, old_sigint)

    elapsed = time.monotonic() - start_time
    print(
        f"[factory] Shutting down gracefully after {cycle} cycles."
        f" Total runtime: {elapsed:.0f}s"
    )
    return 0


def _emit_cli_event(project_path: Path, event_type: str, data: dict) -> None:
    """Emit a factory event, swallowing errors."""
    try:
        from factory.events import emit_event

        emit_event(project_path, event_type, data=data)
    except Exception:
        pass


# ── parser construction ────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Remote Factory — domain-agnostic multi-agent software evolution loop",
    )
    sub = parser.add_subparsers(dest="command")

    # home
    sub.add_parser("home", help="Print factory installation root directory")

    # detect
    p = sub.add_parser("detect", help="Print project state")
    p.add_argument("path", help="Path to the project")

    # discover
    p = sub.add_parser("discover", help="Introspect project and generate eval profile")
    p.add_argument("path", help="Path to the project")

    # init
    p = sub.add_parser("init", help="Create .factory/ or reparse factory.md")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--reparse", action="store_true", help="Reparse existing factory.md")

    # eval
    p = sub.add_parser("eval", help="Run project evals, print JSON CompositeScore")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--skip-project-eval", action="store_true", default=False,
                    help="Skip user-defined project eval dimensions (run only hygiene + growth)")

    # guard
    p = sub.add_parser("guard", help="Check guard rules, print violations or 'clean'")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--baseline", required=True, help="Baseline commit SHA")
    p.add_argument("--check-scope", action="store_true", help="Also check file scope")
    p.add_argument("--check-surfaces", action="store_true",
                    help="Also check fixed surface constraints (research mode)")

    # begin
    p = sub.add_parser("begin", help="Start experiment, print ID")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--hypothesis", required=True, help="Experiment hypothesis text")

    # finalize
    p = sub.add_parser("finalize", help="Finalize experiment with verdict")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--id", required=True, type=int, help="Experiment ID")
    p.add_argument("--verdict", required=True, choices=["keep", "revert", "error"],
                    help="Experiment verdict")
    p.add_argument("--hypothesis", default=None, help="Hypothesis text")
    p.add_argument("--summary", default=None, help="Change summary")
    p.add_argument("--cost", default=None, type=float, help="Cost in USD")
    p.add_argument("--issue", default=None, type=int, help="GitHub issue number")
    p.add_argument("--pr", default=None, type=int, help="GitHub PR number")
    p.add_argument("--notes", default=None, help="Additional notes")
    p.add_argument("--score-before", type=float, default=None, help="Eval score before change")
    p.add_argument("--score-after", type=float, default=None, help="Eval score after change")
    p.add_argument("--force", action="store_true", default=False,
                    help="Bypass precheck gate (for pre-existing failures)")

    # history
    p = sub.add_parser("history", help="Print formatted experiment history table")
    p.add_argument("path", help="Path to the project")

    # notify
    p = sub.add_parser("notify", help="Send Telegram digest")
    p.add_argument("path", help="Path to the project")

    # study
    p = sub.add_parser("study", help="Read interaction logs and write observations")
    p.add_argument("path", help="Path to the project")
    p.add_argument(
        "--projects-dir", default=None,
        help="Directory containing factory-managed projects for cross-project insights",
    )
    p.add_argument(
        "--focus", default=None,
        help="Targeted mode: filter observations to a single backlog item",
    )

    # backlog-remove (alias: deferred-remove)
    p = sub.add_parser("backlog-remove", aliases=["deferred-remove"], help="Remove a completed backlog item")
    p.add_argument("path", help="Path to the project")
    p.add_argument("item", help="Exact text of the backlog item to remove")

    # backlog-list (alias: deferred-list)
    p = sub.add_parser("backlog-list", aliases=["deferred-list"], help="List pending backlog items")
    p.add_argument("path", help="Path to the project")

    # backlog-add
    p = sub.add_parser("backlog-add", help="Add a new item to the backlog")
    p.add_argument("path", help="Path to the project")
    p.add_argument("item", help="Text of the backlog item to add")

    # status
    p = sub.add_parser("status", help="Print project status summary")
    p.add_argument("path", help="Path to the project")

    # summary
    p = sub.add_parser("summary", help="Generate end-of-session summary report")
    p.add_argument("path", help="Path to the project")

    # leakage-check
    p = sub.add_parser("leakage-check", help="Scan text for ground truth leakage against fixed surfaces")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--text", default=None, help="Text to scan for leakage (hypothesis, strategy, etc.)")
    p.add_argument("--text-file", default=None, help="Path to file containing text to scan (safer for large diffs)")
    p.add_argument("--sensitivity", choices=["low", "medium", "high"], default="medium",
                    help="Sensitivity level (default: medium)")

    # validate-research
    p = sub.add_parser("validate-research", help="Validate research mode configuration for ground truth isolation")
    p.add_argument("path", help="Path to the project")

    # backfill-citations
    p = sub.add_parser("backfill-citations", help="Extract citations from experiment text into citations.json")
    p.add_argument("path", help="Path to the project")

    # research
    p = sub.add_parser("research", help="Print research citation index for experiments")
    p.add_argument("path", help="Path to the project")

    # diff
    p = sub.add_parser("diff", help="Compare two experiments side-by-side")
    p.add_argument("path", help="Path to the project")
    p.add_argument("id_a", type=int, help="First experiment ID")
    p.add_argument("id_b", type=int, help="Second experiment ID")

    # explain
    p = sub.add_parser("explain", help="Explain a single experiment with FEEC analysis")
    p.add_argument("path", help="Path to the project")
    p.add_argument("id", type=int, help="Experiment ID")

    # export
    p = sub.add_parser("export", help="Export complete project snapshot as JSON to stdout")
    p.add_argument("path", help="Path to the project")

    # insights
    p = sub.add_parser("insights", help="Cross-project analysis of experiment histories")
    p.add_argument("path", help="Path to the project (insights.md written here)")
    p.add_argument(
        "--projects-dir", default=None,
        help="Directory containing factory-managed projects (default: from registry or ~/factory-projects)",
    )

    # report-update
    p = sub.add_parser("report-update", help="Generate performance report for a project")
    p.add_argument("path", help="Path to the project")

    # registry-list
    sub.add_parser("registry-list", help="List all registered factory-managed projects")

    # ace
    p = sub.add_parser("ace", help="Run ACE self-improvement on agent playbooks")
    p.add_argument("path", help="Path to the project")
    p.add_argument(
        "--projects-dir", default=None,
        help="Directory containing factory-managed projects (default: from registry or ~/factory-projects)",
    )
    p.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Print candidates without writing playbooks",
    )

    # ace-stats
    sub.add_parser("ace-stats", help="Print playbook item counters for all roles")

    # digest
    p = sub.add_parser("digest", help="Summarize recent factory activity across projects")
    p.add_argument("--date", default=None, help="Show activity for a specific date (YYYY-MM-DD)")
    p.add_argument("--days", type=int, default=7, help="Number of days to look back (default: 7)")

    # archive
    p = sub.add_parser("archive", help="Write experiment notes to Obsidian vault")
    p.add_argument("path", help="Path to the project")

    # precheck
    p = sub.add_parser("precheck", help="Run hard precheck gate before keep/revert decision")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--score-before", type=float, default=None, help="Eval score before change")
    p.add_argument("--score-after", type=float, default=None, help="Eval score after change")
    p.add_argument("--hypothesis", default=None, help="Current experiment hypothesis")
    p.add_argument("--baseline", default=None, help="Baseline commit SHA for scope check")
    p.add_argument("--similarity-threshold", type=float, default=0.6,
                    help="Similarity threshold for anti-pattern detection (default: 0.6)")

    # review
    p = sub.add_parser("review", help="Format and post a structured review on a GitHub PR")
    p.add_argument("--verdict", required=True, choices=["keep", "revert", "KEEP", "REVERT"],
                    help="Review verdict")
    p.add_argument("--reason", default=None, help="One-sentence reason for the verdict")
    p.add_argument("--score-before", type=float, default=None, help="Score before change")
    p.add_argument("--score-after", type=float, default=None, help="Score after change")
    p.add_argument("--threshold", type=float, default=0.8, help="Eval threshold")
    p.add_argument("--guards", default=None,
                    help="Guard results as 'check:PASS,check:FAIL' pairs")
    p.add_argument("--precheck-summary", default=None, help="Precheck gate output summary")
    p.add_argument("--code-notes", default=None,
                    help="Code review notes separated by | (pipe)")
    p.add_argument("--experiment-id", type=int, default=None, help="Experiment ID")
    p.add_argument("--hypothesis", default=None, help="Experiment hypothesis text")
    p.add_argument("--pr", type=int, default=None, help="PR number to post review on")
    p.add_argument("--repo", default=None, help="GitHub repo (owner/name) for the PR")
    p.add_argument("--dry-run", action="store_true", default=False,
                    help="Print review without posting")

    # checkpoint
    p = sub.add_parser("checkpoint", help="Show or save a CEO checkpoint for crash-resilient resume")
    p.add_argument("path", help="Path to the project")
    ckpt_action = p.add_mutually_exclusive_group()
    ckpt_action.add_argument("--save", action="store_true", default=False, help="Save a checkpoint")
    ckpt_action.add_argument("--clear", action="store_true", default=False,
                              help="Clear the checkpoint file")
    p.add_argument("--mode", default=None, help="CEO mode (e.g. improve, build)")
    p.add_argument("--experiment", type=int, default=None, help="Active experiment ID")
    p.add_argument("--completed", default=None,
                    help="Comma-separated list of completed agent roles")
    p.add_argument("--pending", default=None,
                    help="Comma-separated list of pending agent roles")
    p.add_argument("--scores", default=None,
                    help="JSON dict of eval scores (e.g. '{\"tests\": 0.9}')")
    p.add_argument("--hypothesis", default=None, help="Current hypothesis text")
    p.add_argument("--completed-hypotheses", default=None,
                    help="Comma-separated list of completed experiment IDs (e.g. '1,2,3')")

    # resume
    p = sub.add_parser("resume", help="Load checkpoint and display resume context")
    p.add_argument("path", help="Path to the project")

    # log
    p = sub.add_parser("log", help="Append a structured event to .factory/events.jsonl")
    p.add_argument("path", help="Path to the project")
    p.add_argument("event_type", help="Event type (e.g. phase.research.completed)")
    p.add_argument("--data", help="JSON data payload")
    p.add_argument("--agent", help="Agent name to attribute the event to")

    # vault-init
    p = sub.add_parser("vault-init", help="Create the factory Obsidian vault")

    # message — send a directive to the CEO
    p = sub.add_parser("message", help="Send a message to the CEO for the next cycle")
    p.add_argument("path", help="Path to the project")
    p.add_argument("text", help="Message text")

    # self-update
    sub.add_parser("self-update", help="Upgrade the factory CLI to the latest version")

    # install — install Factory agents as Claude Code agents
    p = sub.add_parser("install", help="Install Factory agents as Claude Code agents (~/.claude/agents/)")
    p.add_argument(
        "--role",
        default=None,
        help="Install only a specific agent role (default: all)",
    )

    # serve-mcp — MCP stdio server
    sub.add_parser("serve-mcp", help="Start the Factory MCP stdio server")

    # dashboard — live web dashboard
    p = sub.add_parser("dashboard", help="Launch the live Factory dashboard")
    p.add_argument(
        "--projects-dir", default="~/factory-projects",
        help="Directory containing factory-managed projects (default: ~/factory-projects)",
    )
    p.add_argument("--port", type=int, default=8420, help="Server port (default: 8420)")
    p.add_argument("--host", default="0.0.0.0", help="Server host (default: 0.0.0.0)")

    # config — user configuration management
    config_parser = sub.add_parser("config", help="Manage ~/.factory/config.toml")
    config_sub = config_parser.add_subparsers(dest="config_command")
    p_show = config_sub.add_parser("show", help="Show resolved config (secrets masked)")
    p_show.add_argument("--reveal", action="store_true", default=False,
                        help="Show full secret values instead of masking")
    config_sub.add_parser("edit", help="Open config.toml in $EDITOR")
    config_sub.add_parser("migrate", help="Create starter config.toml from current env vars")

    # emit — emit a structured event to .factory/events.jsonl
    p = sub.add_parser("emit", help="Emit a structured event to .factory/events.jsonl")
    p.add_argument("event_type", help="Event type (e.g. agent.started, agent.completed)")
    p.add_argument("--agent", default=None, help="Agent role name")
    p.add_argument("--project", default=".", help="Project path")
    p.add_argument("--data", default=None, help="JSON string of additional event data")

    # agent — invoke a specialist agent directly
    p = sub.add_parser("agent", help="Invoke a specialist agent with a task")
    p.add_argument("role", choices=["researcher", "strategist", "builder", "reviewer",
                                     "evaluator", "archivist", "distiller", "ceo",
                                     "failure_analyst"],
                    help="Agent role to invoke")
    p.add_argument("--task", required=True, help="Task description for the agent")
    p.add_argument("--project", required=True, help="Path to the project")
    p.add_argument("--timeout", type=float, default=600.0,
                    help="Timeout in seconds (default: 600)")
    p.add_argument("--model", default=None,
                    help="Claude model for agent subprocess (default: FACTORY_MODEL env var, or claude CLI default)")
    p.add_argument("--runner", choices=["claude", "bob"], default=None,
                    help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')")
    p.add_argument("--profile", default=None,
                    help="Credential profile from ~/.factory/config.toml")

    # ceo — launch the Factory CEO agent directly
    p = sub.add_parser("ceo", help="Launch the Factory CEO agent (interactive by default)")
    p.add_argument("path", nargs="?", default=None,
                    help="Project path, GitHub URL, idea file path, or prompt. "
                         "In interactive mode, pass a raw idea string")
    p.add_argument(
        "--prompt", default=None,
        help="Path to a prompt/spec file (absolute or relative to project). "
             "Loaded as the build spec into .factory/strategy/current.md",
    )
    p.add_argument(
        "--mode",
        choices=["auto", "auto-fresh", "build", "discover", "improve", "meta", "interactive", "research"],
        default="auto",
        help="Run mode: auto (default, respects in-flight cycle), auto-fresh (ignores in-flight cycle), "
             "build, discover, improve, meta, interactive (research + brainstorm → spec → build), "
             "or research (autonomous research optimization)",
    )
    p.add_argument(
        "--focus", default=None,
        help="Target a specific item: backlog name ('dashboard UI'), issue number (42), "
             "URL (https://github.com/o/r/issues/42), or shorthand (owner/repo#42). "
             "Issue refs are auto-detected and fetched via gh/glab CLI",
    )
    p.add_argument(
        "--dir", default=None,
        help="Working directory name for the new project (overrides auto-derived name from prompt or idea file). "
             "Ignored when pointing at an existing directory or GitHub URL.",
    )
    p.add_argument(
        "--headless", action="store_true", default=False,
        help="Run in pipe mode (non-interactive) instead of foreground",
    )
    p.add_argument(
        "--discover-only", action="store_true", default=False,
        help="Only run discovery and review — do not chain into improve",
    )
    p.add_argument(
        "--no-github", action="store_true", default=False,
        help="Disable GitHub operations (issue creation, PR posting, cloning)",
    )
    p.add_argument("--min-growth", type=int, default=None,
                    help="Minimum guaranteed growth hypotheses (default: 2)")
    p.add_argument("--max-new", type=int, default=None,
                    help="Max new items added to backlog per cycle (default: 2)")
    p.add_argument("--branch", default=None,
                    help="Target branch for PRs (default: from factory.md, fallback: main)")
    p.add_argument("--model", default=None,
                    help="Claude model for agent subprocesses (default: FACTORY_MODEL env var, or claude CLI default)")
    p.add_argument("--runner", choices=["claude", "bob"], default=None,
                    help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')")
    p.add_argument("--profile", default=None,
                    help="Credential profile from ~/.factory/config.toml")

    # run
    p = sub.add_parser("run", help="Run factory cycle (delegates to CEO agent)")
    p.add_argument("path", help="Project path, GitHub URL, idea file path, or prompt")
    p.add_argument(
        "--prompt", default=None,
        help="Path to a prompt/spec file (absolute or relative to project). "
             "Loaded as the build spec into .factory/strategy/current.md",
    )
    p.add_argument(
        "--mode",
        choices=["auto", "auto-fresh", "build", "discover", "improve", "meta", "research"],
        default="auto",
        help="Run mode: auto (default, respects in-flight cycle), auto-fresh (ignores in-flight cycle), "
             "build, discover, improve, meta, or research",
    )
    p.add_argument(
        "--focus", default=None,
        help="Target a specific item: backlog name ('dashboard UI'), issue number (42), "
             "URL (https://github.com/o/r/issues/42), or shorthand (owner/repo#42). "
             "Issue refs are auto-detected and fetched via gh/glab CLI",
    )
    p.add_argument(
        "--discover-only", action="store_true", default=False,
        help="Only run discovery and review — do not chain into improve",
    )
    p.add_argument(
        "--no-github", action="store_true", default=False,
        help="Disable GitHub operations (issue creation, PR posting, cloning)",
    )
    p.add_argument(
        "--loop", action="store_true", default=False,
        help="Enable heartbeat mode: run continuously with sleep between cycles",
    )
    p.add_argument(
        "--interval", type=int, default=1800,
        help="Seconds to sleep between cycles (default: 1800)",
    )
    p.add_argument(
        "--max-cycles", type=int, default=None,
        help="Maximum number of cycles (default: unlimited)",
    )
    p.add_argument("--min-growth", type=int, default=None,
                    help="Minimum guaranteed growth hypotheses (default: 2)")
    p.add_argument("--max-new", type=int, default=None,
                    help="Max new items added to backlog per cycle (default: 2)")
    p.add_argument("--branch", default=None,
                    help="Target branch for PRs (default: from factory.md, fallback: main)")
    p.add_argument("--model", default=None,
                    help="Claude model for agent subprocesses (default: FACTORY_MODEL env var, or claude CLI default)")
    p.add_argument("--runner", choices=["claude", "bob"], default=None,
                    help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')")
    p.add_argument("--profile", default=None,
                    help="Credential profile from ~/.factory/config.toml")

    # tmux — launch factory run in a detached tmux session
    p = sub.add_parser("tmux", help="Launch factory run in a detached tmux session")
    p.add_argument("path", help="Path to the project")
    p.add_argument("--session", default=None, help="Custom tmux session name")
    p.add_argument(
        "--mode",
        choices=["auto", "auto-fresh", "build", "discover", "improve", "meta", "research"],
        default="auto",
        help="Run mode (default: auto, respects in-flight cycle)",
    )
    p.add_argument("--loop", action="store_true", default=False, help="Enable loop mode")
    p.add_argument("--interval", type=int, default=1800, help="Loop interval in seconds")
    p.add_argument("--max-cycles", type=int, default=None, help="Max cycles for loop mode")
    p.add_argument("--attach", action="store_true", default=False,
                    help="Attach to session after creating")
    p.add_argument(
        "--no-github", action="store_true", default=False,
        help="Disable GitHub operations (issue creation, PR posting, cloning)",
    )
    p.add_argument("--model", default=None,
                    help="Claude model for agent subprocesses (default: FACTORY_MODEL env var, or claude CLI default)")
    p.add_argument("--runner", choices=["claude", "bob"], default=None,
                    help="CLI backend to use (default: FACTORY_RUNNER env var, or 'claude')")

    # tmux-ls — list factory tmux sessions
    sub.add_parser("tmux-ls", help="List running factory tmux sessions")

    # tmux-stop — stop factory tmux sessions
    p = sub.add_parser("tmux-stop", help="Stop factory tmux session(s)")
    p.add_argument("--session", default=None, help="Session name to stop")
    p.add_argument("--path", default=None, help="Project path (derives session name)")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    handlers = {
        "home": cmd_home,
        "detect": cmd_detect,
        "discover": cmd_discover,
        "init": cmd_init,
        "eval": cmd_eval,
        "guard": cmd_guard,
        "begin": cmd_begin,
        "finalize": cmd_finalize,
        "history": cmd_history,
        "notify": cmd_notify,
        "study": cmd_study,
        "backlog-remove": cmd_backlog_remove,
        "deferred-remove": cmd_backlog_remove,
        "backlog-list": cmd_backlog_list,
        "deferred-list": cmd_backlog_list,
        "backlog-add": cmd_backlog_add,
        "status": cmd_status,
        "summary": cmd_summary,
        "research": cmd_research,
        "backfill-citations": cmd_backfill_citations,
        "diff": cmd_diff,
        "explain": cmd_explain,
        "export": cmd_export,
        "insights": cmd_insights,
        "report-update": cmd_report_update,
        "registry-list": cmd_registry_list,
        "ace": cmd_ace,
        "ace-stats": cmd_ace_stats,
        "digest": cmd_digest,
        "archive": cmd_archive,
        "precheck": cmd_precheck,
        "leakage-check": cmd_leakage_check,
        "validate-research": cmd_validate_research,
        "review": cmd_review,
        "checkpoint": cmd_checkpoint,
        "resume": cmd_resume,
        "log": cmd_log,
        "vault-init": cmd_vault_init,
        "message": cmd_message,
        "self-update": cmd_self_update,
        "install": cmd_install,
        "serve-mcp": cmd_serve_mcp,
        "dashboard": cmd_dashboard,
        "config": cmd_config,
        "emit": cmd_emit,
        "agent": cmd_agent,
        "ceo": cmd_ceo,
        "run": cmd_run,
        "tmux": cmd_tmux,
        "tmux-ls": cmd_tmux_ls,
        "tmux-stop": cmd_tmux_stop,
    }

    try:
        return handlers[args.command](args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
