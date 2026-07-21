"""Resolve [[graph:...]] reference links in SPEC.md into graph query results."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import networkx as nx
import structlog

log = structlog.get_logger()

_TYPED_REF = re.compile(r"\[\[graph:(entity|path|query|community):(.+?)\]\]")
_BARE_REF = re.compile(r"\[\[graph:(.+?)\]\]")


def load_graph(project_path: Path) -> nx.DiGraph | None:
    """Load .factory/graphify-out/graph.json into a NetworkX DiGraph.

    Returns None if graph file is absent or malformed.
    """
    from factory.graph import load_graph_data

    data = load_graph_data(project_path)
    if data is None:
        return None

    try:
        G: nx.DiGraph = nx.DiGraph()
        for node in data.get("nodes", []):
            node_id = node.get("id", node.get("name", ""))
            if node_id:
                G.add_node(node_id, **{k: v for k, v in node.items() if k != "id"})

        for edge in data.get("edges", data.get("links", [])):
            source = edge.get("source", edge.get("from", ""))
            target = edge.get("target", edge.get("to", ""))
            if source and target:
                G.add_edge(
                    source,
                    target,
                    **{
                        k: v for k, v in edge.items() if k not in ("source", "target", "from", "to")
                    },
                )

        log.debug("resolver.load_graph", nodes=G.number_of_nodes(), edges=G.number_of_edges())
        return G
    except Exception as exc:
        log.warning("resolver.load_graph.failed", error=str(exc))
        return None


def _find_node(name: str, graph: nx.DiGraph) -> str | None:
    """Find a node by exact match, dot-to-underscore mapping, or suffix match."""
    if name in graph:
        return name
    underscore_name = name.replace(".", "_")
    if underscore_name in graph:
        return underscore_name
    underscore_lower = underscore_name.lower()
    if underscore_lower in graph:
        return underscore_lower
    for node_id in graph.nodes:
        if str(node_id).endswith(f".{name}") or str(node_id).endswith(f"/{name}"):
            return node_id
    name_lower = name.lower()
    for node_id in graph.nodes:
        if str(node_id).lower() == name_lower:
            return node_id
    return None


def resolve_entity(name: str, graph: nx.DiGraph) -> str:
    """Look up a node by name and return a compact summary."""
    node_id = _find_node(name, graph)
    if node_id is None:
        return f"[Entity '{name}' not found in graph]"

    attrs = graph.nodes[node_id]
    parts = [f"**{node_id}**"]

    node_type = attrs.get("type", attrs.get("kind", ""))
    if node_type:
        parts.append(f"Type: {node_type}")

    file_path = attrs.get("file", attrs.get("path", ""))
    if file_path:
        line = attrs.get("line", "")
        loc = f"{file_path}:{line}" if line else str(file_path)
        parts.append(f"Location: {loc}")

    community = attrs.get("community", attrs.get("group", ""))
    if community:
        parts.append(f"Community: {community}")

    in_degree = graph.in_degree(node_id)
    out_degree = graph.out_degree(node_id)
    parts.append(f"Dependencies: {out_degree} outgoing, {in_degree} incoming")

    return " | ".join(parts)


def resolve_path(from_node: str, to_node: str, graph: nx.DiGraph) -> str:
    """Find shortest path between two entities and return formatted listing."""
    source = _find_node(from_node, graph)
    target = _find_node(to_node, graph)

    if source is None:
        return f"[Source entity '{from_node}' not found in graph]"
    if target is None:
        return f"[Target entity '{to_node}' not found in graph]"

    try:
        path = nx.shortest_path(graph, source, target)
        return " → ".join(path)
    except nx.NetworkXNoPath:
        try:
            path = nx.shortest_path(graph, target, source)
            return " → ".join(path) + " (reverse direction)"
        except nx.NetworkXNoPath:
            return f"[No path between '{from_node}' and '{to_node}']"
    except nx.NodeNotFound as exc:
        return f"[Path error: {exc}]"


def resolve_query(query: str, project_path: Path) -> str:
    """Run graphify query CLI and return the result."""
    try:
        result = subprocess.run(
            ["graphify", "query", query, "--project-dir", str(project_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return f"[Query '{query}' returned no results]"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return f"[Query '{query}' failed — graphify CLI not available]"


def resolve_community(name: str, graph: nx.DiGraph) -> str:
    """List all nodes in a detected community/subsystem."""
    members = []
    name_lower = name.lower()
    for node_id, attrs in graph.nodes(data=True):
        community = str(attrs.get("community", attrs.get("group", ""))).lower()
        if community == name_lower:
            members.append(str(node_id))

    if not members:
        for node_id in graph.nodes:
            node_str = str(node_id)
            if name_lower in node_str.lower():
                members.append(node_str)

    if not members:
        return f"[Community '{name}' not found in graph]"

    members.sort()
    if len(members) > 20:
        shown = members[:20]
        return ", ".join(shown) + f" … and {len(members) - 20} more"
    return ", ".join(members)


def _resolve_typed_match(
    match: re.Match[str],
    graph: nx.DiGraph | None,
    project_path: Path,
) -> str:
    """Resolve a typed [[graph:type:value]] reference."""
    ref_type = match.group(1)
    value = match.group(2).strip()

    if graph is None:
        return match.group(0)

    if ref_type == "entity":
        return resolve_entity(value, graph)
    elif ref_type == "path":
        parts = value.split(":")
        if len(parts) == 2:
            return resolve_path(parts[0].strip(), parts[1].strip(), graph)
        return f"[Invalid path reference: expected 'A:B', got '{value}']"
    elif ref_type == "query":
        return resolve_query(value, project_path)
    elif ref_type == "community":
        return resolve_community(value, graph)

    return match.group(0)


def _resolve_bare_match(
    match: re.Match[str],
    graph: nx.DiGraph | None,
) -> str:
    """Resolve a bare [[graph:name]] reference as entity lookup."""
    if graph is None:
        return match.group(0)
    name = match.group(1).strip()
    return resolve_entity(name, graph)


def resolve_references(spec_content: str, project_path: Path) -> str:
    """Find all [[graph:...]] patterns in spec text and resolve them.

    If graph is unavailable, returns spec_content unchanged.
    """
    graph = load_graph(project_path)

    result = _TYPED_REF.sub(
        lambda m: _resolve_typed_match(m, graph, project_path),
        spec_content,
    )

    result = _BARE_REF.sub(
        lambda m: _resolve_bare_match(m, graph),
        result,
    )

    return result
