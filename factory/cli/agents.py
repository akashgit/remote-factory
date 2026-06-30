"""CLI agents commands."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import structlog
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from collections.abc import Callable
from typing import TYPE_CHECKING

log = structlog.get_logger()

from factory.cli._helpers import _emit_cli_event, _run
from factory.cli.ceo import _resolve_background, _resolve_model, _resolve_runner, _resolve_tmux_persist

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


def cmd_agent(args: argparse.Namespace) -> int:
    """Invoke a specialist agent with the given task."""
    from factory.agents.plugin import load_agent_config
    from factory.agents.runner import invoke_agent
    from factory.user_config import load_config

    profile = getattr(args, "profile", None)
    load_config(profile=profile)

    role = args.role
    task = args.task
    project_path = Path(args.project).resolve()
    timeout = getattr(args, "timeout", 600.0)
    model = _resolve_model(args)
    if not model:
        agent_config = load_agent_config()
        if role in agent_config:
            model = agent_config[role].model or None
    runner = _resolve_runner(args)
    use_profile = getattr(args, "use_profile", False)
    tmux_persist = _resolve_tmux_persist(args)
    background = _resolve_background(args)
    if background and tmux_persist:
        print("Error: --bg and --tmux-persist are mutually exclusive.", file=sys.stderr)
        return 1
    review_tag = getattr(args, "review_tag", None)
    parent_span = getattr(args, "parent_session", None) or os.environ.get("FACTORY_PARENT_SPAN_ID")
    if parent_span:
        os.environ["FACTORY_PARENT_SPAN_ID"] = parent_span

    result, code = _run(invoke_agent(
        role,
        task,
        project_path,
        timeout=timeout,
        dangerously_skip_permissions=True,
        model=model,
        runner_name=runner,
        use_profile=use_profile,
        tmux_persist=tmux_persist,
        background=background,
        review_tag=review_tag,
    ))
    print(result)
    return code


def cmd_runners_list(args: argparse.Namespace) -> int:
    """List all available runners with metadata."""
    from factory.runners import get_all_runner_meta

    meta_list = get_all_runner_meta()
    use_json = getattr(args, "json", False)

    if use_json:
        import json as json_mod
        data = []
        for m in meta_list:
            data.append({
                "name": m.name,
                "display_name": m.display_name,
                "binary": m.binary,
                "install_hint": m.install_hint,
                "available": m.is_available(),
                "auth_ok": m.check_auth(),
                "supports_model_override": m.supports_model_override,
                "supports_interactive": m.supports_interactive,
                "supports_streaming": m.supports_streaming,
                "supports_usage_telemetry": m.supports_usage_telemetry,
                "supports_session_name": m.supports_session_name,
            })
        print(json_mod.dumps(data, indent=2))
        return 0

    if not meta_list:
        print("No runners registered.")
        return 0

    header = f"{'Name':<12} {'Display':<20} {'Binary':<12} {'Available':>9} {'Auth':>6}"
    print(header)
    print("-" * len(header))
    for m in meta_list:
        avail = "yes" if m.is_available() else "no"
        auth = "ok" if m.check_auth() else "missing"
        print(f"{m.name:<12} {m.display_name:<20} {m.binary:<12} {avail:>9} {auth:>6}")
    return 0

