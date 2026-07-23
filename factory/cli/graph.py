"""Graph subcommands — extract, update, status."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from factory.cli._helpers import _emit_cli_event


def cmd_graph_extract(args: argparse.Namespace) -> int:
    """Run graphify extract on a project."""
    from factory.graph import extract_graph, is_graphify_installed

    project_path = Path(args.path).resolve()
    if not project_path.is_dir():
        print(f"Error: not a directory: {project_path}", file=sys.stderr)
        return 1

    if not is_graphify_installed():
        print(
            "Error: graphify CLI not found on PATH. Install with: uv tool install graphifyy",
            file=sys.stderr,
        )
        return 1

    _emit_cli_event(project_path, "graph.extract.started", {"path": str(project_path)})
    result = extract_graph(project_path)
    if result is None:
        print("Error: graph extraction failed (check logs for details)", file=sys.stderr)
        _emit_cli_event(project_path, "graph.extract.failed", {})
        return 1

    _emit_cli_event(project_path, "graph.extract.completed", {"output": str(result)})
    print(f"Graph extracted: {result}")
    return 0


def cmd_graph_update(args: argparse.Namespace) -> int:
    """Run incremental graphify update on a project."""
    from factory.graph import is_graph_available, is_graphify_installed, update_graph

    project_path = Path(args.path).resolve()
    if not project_path.is_dir():
        print(f"Error: not a directory: {project_path}", file=sys.stderr)
        return 1

    if not is_graphify_installed():
        print(
            "Error: graphify CLI not found on PATH. Install with: uv tool install graphifyy",
            file=sys.stderr,
        )
        return 1

    if not is_graph_available(project_path):
        print(
            "No existing graph found — running full extraction instead.",
            file=sys.stderr,
        )
        from factory.graph import extract_graph

        result = extract_graph(project_path)
    else:
        result = update_graph(project_path)

    if result is None:
        print("Error: graph update failed (check logs for details)", file=sys.stderr)
        return 1

    print(f"Graph updated: {result}")
    return 0


def cmd_graph_status(args: argparse.Namespace) -> int:
    """Show graph freshness and node/edge counts."""
    from factory.graph import graph_stats, is_graph_available, is_graph_stale, is_graphify_installed

    project_path = Path(args.path).resolve()
    if not project_path.is_dir():
        print(f"Error: not a directory: {project_path}", file=sys.stderr)
        return 1

    print(f"Project: {project_path}")
    print(f"Graphify installed: {'yes' if is_graphify_installed() else 'no'}")

    if not is_graph_available(project_path):
        print("Graph: not available (run 'factory graph extract' first)")
        return 0

    stats = graph_stats(project_path)
    if stats:
        print(f"Nodes: {stats['nodes']}")
        print(f"Edges: {stats['edges']}")

    staleness = is_graph_stale(project_path)
    if staleness is True:
        print("Freshness: STALE (graph is older than latest commit)")
    elif staleness is False:
        print("Freshness: FRESH")
    else:
        print("Freshness: unknown (could not compare timestamps)")

    return 0
