"""Spec generation orchestration — graphify extraction + single annotator agent."""

from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from pathlib import Path

import structlog

log = structlog.get_logger()

EXCLUDED_DIRS = frozenset(
    {
        "node_modules",
        ".factory",
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        ".eggs",
        "*.egg-info",
    }
)

SOURCE_EXTENSIONS = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".rb",
        ".ex",
        ".exs",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".swift",
        ".scala",
        ".clj",
        ".proto",
        ".graphql",
        ".sql",
    }
)

BATCH_TOKEN_LIMIT = 80_000
APPROX_CHARS_PER_TOKEN = 4
GRAPH_SUMMARY_CHAR_LIMIT = 80_000


def _get_gitignored(paths: list[Path], project_path: Path) -> set[Path]:
    """Return the subset of paths that are gitignored, using a single subprocess."""
    if not paths:
        return set()
    result = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        input="\n".join(str(p) for p in paths),
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode not in (0, 1):
        return set()
    return {Path(line) for line in result.stdout.splitlines() if line}


def _is_excluded_dir(part: str) -> bool:
    """Check if a directory component matches an exclusion pattern."""
    for excluded in EXCLUDED_DIRS:
        if excluded.startswith("*"):
            if part.endswith(excluded[1:]):
                return True
        elif part == excluded:
            return True
    return False


def collect_source_files(project_path: Path) -> list[Path]:
    """Collect source files from a project, respecting .gitignore and exclusions.

    Uses os.walk with pruning so excluded directories are never descended into.
    Returns paths relative to project_path, sorted for deterministic output.
    """
    has_git = (project_path / ".git").is_dir()
    candidates: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = sorted(d for d in dirnames if not _is_excluded_dir(d))
        for fname in filenames:
            full = Path(dirpath) / fname
            if full.suffix not in SOURCE_EXTENSIONS:
                continue
            candidates.append(full.relative_to(project_path))

    candidates.sort()

    if has_git and candidates:
        ignored = _get_gitignored([project_path / c for c in candidates], project_path)
        candidates = [c for c in candidates if (project_path / c) not in ignored]

    log.info("spec.collect_source_files", count=len(candidates), project=str(project_path))
    return candidates


def group_into_batches(
    files: list[Path],
    project_path: Path,
    token_limit: int = BATCH_TOKEN_LIMIT,
) -> list[list[Path]]:
    """Group source files into batches that fit within a token limit.

    Each batch contains files whose combined content fits within the limit.
    Files larger than the limit are placed in their own batch.
    """
    char_limit = token_limit * APPROX_CHARS_PER_TOKEN
    batches: list[list[Path]] = []
    current_batch: list[Path] = []
    current_chars = 0

    for rel_path in files:
        full_path = project_path / rel_path
        try:
            file_chars = full_path.stat().st_size
        except OSError:
            continue

        if file_chars > char_limit:
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                current_chars = 0
            log.warning(
                "spec.batch.oversized_file",
                file=str(rel_path),
                size=file_chars,
                limit=char_limit,
            )
            batches.append([rel_path])
            continue

        if current_batch and current_chars + file_chars > char_limit:
            batches.append(current_batch)
            current_batch = []
            current_chars = 0

        current_batch.append(rel_path)
        current_chars += file_chars

    if current_batch:
        batches.append(current_batch)

    log.info(
        "spec.group_into_batches",
        total_files=len(files),
        batches=len(batches),
        token_limit=token_limit,
    )
    return batches


def _qualified_name(node: dict) -> str:
    """Derive a Python-style qualified name from graphify node attributes.

    Uses source_file + label to produce names like:
      factory.ceo_completion              (module)
      factory.ceo_completion.read_cycle_state  (function)
      factory.ceo_completion.IncompleteGap     (class)
    """
    source_file = node.get("source_file", "")
    label = node.get("label", node.get("id", "?"))

    if not source_file:
        return label

    module = source_file.removesuffix(".py").replace("/", ".")

    if label.endswith(".py") or label == source_file:
        return module
    func_name = label.removesuffix("()")
    return f"{module}.{func_name}"


def _entity_type(node: dict) -> str:
    """Infer entity type from graphify label convention."""
    label = node.get("label", "")
    if label.endswith(".py") or label == node.get("source_file", ""):
        return "module"
    if label.endswith("()"):
        return "function"
    if label and label[0].isupper() and not label.endswith(")"):
        return "class"
    return "variable"


def build_graph_summary(graph_data: dict, char_limit: int = GRAPH_SUMMARY_CHAR_LIMIT) -> str:
    """Build a compact text summary of a code knowledge graph for annotator consumption."""
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", graph_data.get("links", []))

    id_to_qname: dict[str, str] = {}
    for node in nodes:
        node_id = node.get("id", "")
        id_to_qname[node_id] = _qualified_name(node)

    lines: list[str] = []
    lines.append(f"# Code Knowledge Graph Summary ({len(nodes)} nodes, {len(edges)} edges)\n")

    by_community: dict[str, list[dict]] = defaultdict(list)
    for node in nodes:
        community = node.get("community", node.get("group", "ungrouped"))
        by_community[str(community)].append(node)

    by_type: dict[str, int] = defaultdict(int)
    for node in nodes:
        by_type[_entity_type(node)] += 1

    lines.append("## Entity Counts by Type\n")
    for ntype, count in sorted(by_type.items(), key=lambda x: -x[1]):
        lines.append(f"- {ntype}: {count}")
    lines.append("")

    edge_types: dict[str, int] = defaultdict(int)
    for edge in edges:
        edge_types[edge.get("type", edge.get("relationship", "unknown"))] += 1

    lines.append("## Relationship Counts\n")
    for etype, count in sorted(edge_types.items(), key=lambda x: -x[1]):
        lines.append(f"- {etype}: {count}")
    lines.append("")

    lines.append("## Communities / Subsystems\n")
    for community_id in sorted(by_community):
        members = by_community[community_id]
        label = f"Community {community_id}" if community_id != "ungrouped" else "Ungrouped"
        lines.append(f"### {label} ({len(members)} entities)\n")

        members_by_type: dict[str, list[str]] = defaultdict(list)
        for m in members:
            mtype = _entity_type(m)
            qname = _qualified_name(m)
            members_by_type[mtype].append(qname)

        for mtype in sorted(members_by_type):
            names = sorted(members_by_type[mtype])
            lines.append(f"**{mtype}:** {', '.join(names)}")
        lines.append("")

        if len("\n".join(lines)) > char_limit:
            lines.append("(truncated — graph summary exceeds size limit)")
            break

    lines.append("## Key Relationships\n")
    remaining = char_limit - len("\n".join(lines))
    rel_lines: list[str] = []
    for edge in edges:
        src_id = edge.get("source", edge.get("from", "?"))
        tgt_id = edge.get("target", edge.get("to", "?"))
        rel = edge.get("type", edge.get("relationship", "?"))
        src = id_to_qname.get(src_id, src_id)
        tgt = id_to_qname.get(tgt_id, tgt_id)
        line = f"- {src} --[{rel}]--> {tgt}"
        rel_lines.append(line)
        if len("\n".join(rel_lines)) > remaining - 100:
            rel_lines.append(f"(... {len(edges) - len(rel_lines)} more relationships)")
            break

    lines.extend(rel_lines)
    lines.append("")

    return "\n".join(lines)


def _build_annotate_prompt(source_context: str, project_path: Path) -> str:
    """Build the annotator agent prompt for producing SPEC.md."""
    return (
        f"Generate a HIGH-LEVEL behavioral overview spec for the project at {project_path}.\n\n"
        f"## Source Context\n\n{source_context}\n\n"
        f"Produce a spec with RFC 2119 normative language.\n\n"
        f"## Format\n\n"
        f"The spec MUST be structured as a two-tier overview document:\n"
        f"- **Tier 1 (in SPEC.md):** Problem statement, goals, non-goals, design philosophy,\n"
        f"  project identity, architecture overview, domain model (entity names and relationships),\n"
        f"  state machines, shared contracts, entry points, security, extension points,\n"
        f"  implementation checklist — all high-level behavioral contracts\n"
        f"- **Tier 2 (graph references):** Where you would normally list granular module dependency\n"
        f"  listings, function-level details, or call relationships, instead insert\n"
        f"  `[[graph:ModuleName]]` reference links that point into the code knowledge graph.\n"
        f"  For example: `[[graph:factory.graph]]`, `[[graph:path:store:registry]]`\n\n"
        f"## Required Section: How to Read the Knowledge Graph\n\n"
        f"Include this section near the end of the spec:\n\n"
        f"```\n"
        f"## How to Read the Knowledge Graph\n\n"
        f"This spec uses `[[graph:...]]` reference links to point into a code knowledge graph\n"
        f"extracted by graphify. The graph contains AST-derived entities (modules, classes,\n"
        f"functions) and their typed relationships (imports, calls, inherits).\n\n"
        f"### Reference Link Types\n\n"
        f"- `[[graph:EntityName]]` — look up a specific entity (module, class, function)\n"
        f"- `[[graph:path:A:B]]` — find the dependency path between entities A and B\n"
        f"- `[[graph:query:question]]` — run a natural language query against the graph\n"
        f"- `[[graph:community:subsystem]]` — list all entities in a detected subsystem\n\n"
        f"### When to Use\n\n"
        f"- **Planning and design:** Read the overview sections in this spec\n"
        f"- **Implementation details:** Resolve `[[graph:...]]` links via "
        f"`factory spec resolve` or query the graph directly with "
        f"`graphify explain`, `graphify path`, `graphify query`\n"
        f"```\n\n"
        f"Write the annotated repo spec to {project_path / 'SPEC.md'}."
    )


async def generate_spec(project_path: Path) -> Path:
    """Generate a repo spec for a project.

    1. Run graphify extract → graph.json (local AST, no LLM cost)
    2. Build a compact graph summary with qualified Python names
    3. Single annotator agent reads summary → produces SPEC.md

    Returns the path to the generated SPEC.md.
    Raises RuntimeError if graphify is not installed or extraction fails.
    """
    from factory.agents.runner import invoke_agent
    from factory.graph import extract_graph, is_graphify_installed, load_graph_data

    if not is_graphify_installed():
        raise RuntimeError(
            "graphify is required for spec generation. Install with: uv tool install graphifyy"
        )

    factory_dir = project_path / ".factory"
    factory_dir.mkdir(parents=True, exist_ok=True)

    graph_path = extract_graph(project_path)
    if graph_path is None:
        raise RuntimeError("graphify extraction failed — check logs for details")

    graph_data = load_graph_data(project_path)
    if graph_data is None:
        raise RuntimeError("graph.json is unreadable after extraction")

    summary = build_graph_summary(graph_data)
    log.info("spec.generate.graph", summary_len=len(summary))

    annotate_task = _build_annotate_prompt(
        f"The following is a structural summary extracted from the code knowledge graph:\n\n"
        f"{summary}",
        project_path,
    )

    result, code = await invoke_agent(
        "researcher",
        annotate_task,
        project_path,
        timeout=600.0,
        dangerously_skip_permissions=True,
    )
    if code != 0:
        raise RuntimeError(f"Spec annotation failed (exit {code}): {result[:500]}")

    repo_spec = project_path / "SPEC.md"
    if not repo_spec.exists():
        raise FileNotFoundError(
            f"Annotation agent did not produce {repo_spec}. Agent output: {result[:500]}"
        )

    log.info("spec.generate.complete", output=str(repo_spec))
    return repo_spec
