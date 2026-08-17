"""Graph subcommands — extract, update, status, query, explain, path."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import structlog

from factory.cli._helpers import _emit_cli_event

log = structlog.get_logger()

_GRAPHIFY_TIMEOUT = 60


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


def _run_graphify(cmd: list[str], project_path: Path, event_prefix: str) -> int:
    """Run a graphify CLI command with timeout, logging, and event emission."""
    _emit_cli_event(project_path, f"{event_prefix}.started", {"cmd": cmd})
    log.info("graphify.run", cmd=cmd)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_GRAPHIFY_TIMEOUT,
            cwd=project_path,
        )
    except subprocess.TimeoutExpired:
        print(f"Error: graphify timed out after {_GRAPHIFY_TIMEOUT}s", file=sys.stderr)
        _emit_cli_event(project_path, f"{event_prefix}.timeout", {})
        return 1

    if result.returncode != 0:
        print(result.stderr or "graphify command failed", file=sys.stderr)
        _emit_cli_event(project_path, f"{event_prefix}.failed", {"rc": result.returncode})
        return 1

    if result.stdout:
        print(result.stdout, end="")
    _emit_cli_event(project_path, f"{event_prefix}.completed", {})
    return 0


def cmd_graph_query(args: argparse.Namespace) -> int:
    """BFS traversal of the knowledge graph."""
    from factory.graph import is_graph_available, is_graphify_installed

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
        print("Error: no graph.json found (run 'factory graph extract' first)", file=sys.stderr)
        return 1

    graph_file = str(project_path / "graph.json")
    cmd = ["graphify", "query", args.question, "--graph", graph_file, "--depth", str(args.depth)]
    return _run_graphify(cmd, project_path, "graph.query")


def cmd_graph_explain(args: argparse.Namespace) -> int:
    """Explain a node and its neighbors in the knowledge graph."""
    from factory.graph import is_graph_available, is_graphify_installed

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
        print("Error: no graph.json found (run 'factory graph extract' first)", file=sys.stderr)
        return 1

    graph_file = str(project_path / "graph.json")
    cmd = ["graphify", "explain", args.node, "--graph", graph_file]
    return _run_graphify(cmd, project_path, "graph.explain")


def cmd_graph_path(args: argparse.Namespace) -> int:
    """Shortest path between two nodes in the knowledge graph."""
    from factory.graph import is_graph_available, is_graphify_installed

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
        print("Error: no graph.json found (run 'factory graph extract' first)", file=sys.stderr)
        return 1

    graph_file = str(project_path / "graph.json")
    cmd = ["graphify", "path", args.source, args.target, "--graph", graph_file]
    return _run_graphify(cmd, project_path, "graph.path")
