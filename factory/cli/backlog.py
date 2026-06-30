"""CLI backlog commands."""
from __future__ import annotations

import argparse
import structlog
import sys
from pathlib import Path

from factory.cli._helpers import _emit_cli_event

log = structlog.get_logger()

def cmd_backlog_remove(args: argparse.Namespace) -> int:
    from factory.study import remove_backlog_item

    project_path = Path(args.path)
    item_text = args.item
    if remove_backlog_item(project_path, item_text):
        _emit_cli_event(project_path, "backlog.removed", {"item": item_text})
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
        _emit_cli_event(project_path, "backlog.added", {"item": item_text})
        print(f"Added backlog item: {item_text}")
        return 0
    print(f"Backlog item already exists: {item_text}", file=sys.stderr)
    return 1

