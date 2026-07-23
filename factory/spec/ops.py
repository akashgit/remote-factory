"""Spec operations — validate, scope, update, and impact via agent calls."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import structlog

log = structlog.get_logger()

# ── Validate ────────────────────────────────────────────────────

VALIDATE_PROMPT = """\
Validate this SPEC.md against the project at {project_path}.

## SPEC.md
{spec_content}

## Checks to perform
1. For each module with a declared path, verify the path exists on disk
2. For modules with declared dependencies, spot-check that actual imports match
3. Flag orphan modules (no other module depends on them or lists them as consumed_by)
4. Check that these sections are non-empty: Problem Statement, Goals, Non-Goals, \
Design Philosophy, Configuration, Security, Extension Points, Implementation Checklist
5. For entity names in the Domain Model section, verify matching classes exist in source
6. Check that module behavioral specs use RFC 2119 normative language (MUST, SHOULD, etc.)

## Output
Write a Markdown validation report with sections for Errors and Warnings.
Errors = blocking issues (path not found, critical structural problems).
Warnings = advisory (missing sections, orphan modules, missing normative language).

End the report with exactly one of these verdict lines on its own line:
Verdict: PASS
Verdict: FAIL

Use FAIL if there are any errors, PASS otherwise.
"""


def _validate_graph_references(spec_content: str, project_path: Path) -> str:
    """Verify that [[graph:...]] entity references resolve to actual graph nodes.

    Returns a Markdown section with validation results, or empty string
    if graph is unavailable or no references found.
    """
    from factory.graph import is_graph_available
    from factory.spec.resolver import _BARE_REF, _TYPED_REF, _find_node, load_graph

    if not is_graph_available(project_path):
        return ""

    graph = load_graph(project_path)
    if graph is None:
        return ""

    resolved: list[str] = []
    orphans: list[str] = []

    seen: set[str] = set()

    for match in _TYPED_REF.finditer(spec_content):
        ref_type = match.group(1)
        value = match.group(2).strip()
        ref_key = f"{ref_type}:{value}"
        if ref_key in seen:
            continue
        seen.add(ref_key)

        if ref_type == "entity":
            if _find_node(value, graph) is not None:
                resolved.append(value)
            else:
                orphans.append(value)
        elif ref_type == "community":
            name_lower = value.lower()
            found = any(
                str(attrs.get("community", attrs.get("group", ""))).lower() == name_lower
                for _, attrs in graph.nodes(data=True)
            )
            if found:
                resolved.append(f"community:{value}")
            else:
                orphans.append(f"community:{value}")

    for match in _BARE_REF.finditer(spec_content):
        name = match.group(1).strip()
        if name in seen:
            continue
        seen.add(name)

        if _find_node(name, graph) is not None:
            resolved.append(name)
        else:
            orphans.append(name)

    if not resolved and not orphans:
        return ""

    lines = ["## Graph Reference Validation"]
    lines.append(f"\nResolved: {len(resolved)} | Orphans: {len(orphans)}")
    if orphans:
        lines.append("\n**Orphan references** (entity not found in graph):")
        for name in sorted(orphans):
            lines.append(f"- `[[graph:{name}]]`")

    return "\n".join(lines)


def _parse_verdict(text: str) -> bool:
    """Extract pass/fail verdict from agent output. Defaults to True if absent."""
    match = re.search(r"^Verdict:\s*(PASS|FAIL)\s*$", text, re.MULTILINE)
    if match:
        return match.group(1) == "PASS"
    return True


async def validate_spec(project_path: Path) -> tuple[str, bool]:
    """Validate SPEC.md against the actual project using a single Haiku agent call.

    Writes the agent's markdown report to .factory/spec_validation.md.
    Returns (report_text, is_valid).
    """
    from factory.agents.runner import invoke_agent
    from factory.spec import read_spec

    spec_content = read_spec(project_path)

    prompt = VALIDATE_PROMPT.format(
        project_path=project_path,
        spec_content=spec_content,
    )

    result_text, code = await invoke_agent(
        "researcher",
        prompt,
        project_path,
        timeout=120.0,
        dangerously_skip_permissions=True,
        model="haiku",
    )

    if code != 0:
        report = (
            f"# Spec Validation Report\n\nValidation agent failed (exit {code}).\n\nVerdict: PASS\n"
        )
        is_valid = True
    else:
        report = result_text.strip()
        is_valid = _parse_verdict(report)

    graph_report = _validate_graph_references(spec_content, project_path)
    if graph_report:
        report += f"\n\n{graph_report}"

    output_path = project_path / ".factory" / "spec_validation.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)

    log.info(
        "spec.validate.complete",
        is_valid=is_valid,
        output=str(output_path),
    )

    return report, is_valid


# ── Scope & Update ──────────────────────────────────────────────

SCOPE_PROMPT = """\
Analyze this git diff against the repo spec and identify which spec modules are affected.

## SPEC.md
{spec_content}

## Git Diff
{diff_text}

## Output
Write a Markdown summary of the affected scope:
- Which existing spec modules are affected by the diff (list module names)
- Which changed files don't map to any existing module (new/unmapped files)
- Which files were deleted in this diff

Use clear headings: "## Affected Modules", "## New Files", "## Deleted Files".
List items as bullet points under each heading, or write "None" if empty.
"""


def _get_diff_text(project_path: Path, experiment_id: int | None, spec_rel: str) -> str:
    """Get diff text from an experiment file or git."""
    if experiment_id is not None:
        diff_path = project_path / ".factory" / "experiments" / str(experiment_id) / "changes.diff"
        if not diff_path.is_file():
            raise FileNotFoundError(f"No diff found at {diff_path}")
        return diff_path.read_text()

    result = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", spec_rel],
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    spec_commit = result.stdout.strip() if result.returncode == 0 else ""
    base_ref = spec_commit or "HEAD~1"

    rev_check = subprocess.run(
        ["git", "rev-parse", "--verify", base_ref],
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if rev_check.returncode != 0:
        result = subprocess.run(
            ["git", "diff", "--root", "HEAD"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git diff failed: {result.stderr[:200]}")
        return result.stdout

    result = subprocess.run(
        ["git", "diff", base_ref, "HEAD"],
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git diff failed: {result.stderr[:200]}")
    return result.stdout


def _graph_context_for_diff(diff_text: str, project_path: Path) -> str:
    """Extract graph dependency context for changed files in a diff.

    Returns a compact summary of graph neighbors for changed files,
    or empty string if graph is unavailable.
    """
    from factory.graph import is_graph_available
    from factory.spec.resolver import _find_node, load_graph

    if not is_graph_available(project_path):
        return ""

    graph = load_graph(project_path)
    if graph is None:
        return ""

    changed_files: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            parts = line.split()
            if len(parts) >= 3:
                path = parts[2].lstrip("a/")
                changed_files.add(path)
        elif line.startswith("+++ b/"):
            changed_files.add(line[6:])

    if not changed_files:
        return ""

    sections: list[str] = []
    for fpath in sorted(changed_files):
        node_id = _find_node(fpath, graph)
        if node_id is None:
            stem = fpath.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            node_id = _find_node(stem, graph)
        if node_id is None:
            continue

        neighbors = sorted(set(graph.successors(node_id)) | set(graph.predecessors(node_id)))
        if neighbors:
            neighbor_list = ", ".join(neighbors[:10])
            extra = f" (+{len(neighbors) - 10} more)" if len(neighbors) > 10 else ""
            sections.append(f"- **{fpath}** → {neighbor_list}{extra}")

    if not sections:
        return ""

    return "Changed files and their graph neighbors:\n" + "\n".join(sections)


async def scope_diff(project_path: Path, experiment_id: int | None = None) -> str:
    """Scope a diff against the existing repo spec using a Haiku agent call.

    If experiment_id is provided, reads .factory/experiments/{id}/changes.diff.
    Otherwise, diffs between HEAD and the commit that last touched SPEC.md.

    Returns the agent's markdown summary of affected scope.
    """
    from factory.agents.runner import invoke_agent
    from factory.discovery.spec import resolve_spec
    from factory.spec import read_spec

    spec_path = resolve_spec(project_path)
    if spec_path is None:
        raise FileNotFoundError(f"No repo spec found in {project_path}")

    spec_content = read_spec(project_path)
    spec_rel = str(spec_path.relative_to(project_path))

    diff_text = _get_diff_text(project_path, experiment_id, spec_rel)

    graph_context = _graph_context_for_diff(diff_text, project_path)
    prompt = SCOPE_PROMPT.format(spec_content=spec_content, diff_text=diff_text)
    if graph_context:
        prompt += f"\n\n## Graph Dependency Context\n{graph_context}"

    result_text, code = await invoke_agent(
        "researcher",
        prompt,
        project_path,
        timeout=120.0,
        dangerously_skip_permissions=True,
        model="haiku",
    )

    if code != 0:
        raise RuntimeError(f"Scope diff agent failed (exit {code})")

    scope_text = result_text.strip()

    scope_path = project_path / ".factory" / "spec_update_scope.md"
    scope_path.parent.mkdir(parents=True, exist_ok=True)
    scope_path.write_text(scope_text)

    log.info("spec.scope_diff", output=str(scope_path))

    return scope_text


async def update_spec(project_path: Path) -> Path:
    """Update the repo spec based on changes since last spec commit.

    1. Scope the diff
    2. Run patcher agent to update SPEC.md

    Returns the path to the updated SPEC.md.
    """
    from factory.agents.runner import invoke_agent
    from factory.discovery.spec import resolve_spec

    spec_path = resolve_spec(project_path)
    if spec_path is None:
        raise FileNotFoundError(f"No repo spec found in {project_path}")

    scope_text = await scope_diff(project_path)

    if not scope_text or scope_text.isspace():
        log.info("spec.update.noop", reason="no changes detected")
        return spec_path

    patch_task = (
        f"Update the repo spec at {spec_path} based on the scoped changes.\n\n"
        f"## Scope of Changes\n{scope_text}\n\n"
        f"Read the existing spec at {spec_path}.\n"
        f"Read the changed source files to understand what changed.\n"
        f"Update affected module entries and add/remove modules as needed.\n"
        f"Write the updated spec to {spec_path}."
    )

    result, code = await invoke_agent(
        "researcher",
        patch_task,
        project_path,
        timeout=300.0,
        dangerously_skip_permissions=True,
        model="opus",
    )
    if code != 0:
        raise RuntimeError(f"Spec patch failed (exit {code}): {result[:500]}")

    log.info("spec.update.complete", output=str(spec_path))
    return spec_path


# ── Impact ──────────────────────────────────────────────────────

IMPACT_PROMPT = """\
Extract an impact analysis for the module "{module_name}" from this repo spec.

## SPEC.md
{spec_content}

## Output
Produce a compact Markdown snippet covering:
1. Module path, role, and classification
2. Dependencies (what it imports)
3. Dependents (what imports it)
4. Contracts owned by this module
5. Change impact (severity and affected modules)

Use the exact heading "## Impact: {module_name}" as the first line.
Keep the output under 30 lines. Return ONLY the Markdown snippet.
"""


def _graph_impact(module_name: str, project_path: Path) -> str | None:
    """Try to produce impact analysis from the code knowledge graph.

    Returns a Markdown snippet or None if graph is unavailable or lookup fails.
    """
    from factory.graph import is_graph_available
    from factory.spec.resolver import _find_node, load_graph

    if not is_graph_available(project_path):
        return None

    graph = load_graph(project_path)
    if graph is None:
        return None

    node_id = _find_node(module_name, graph)
    if node_id is None:
        return None

    attrs = graph.nodes[node_id]
    lines = [f"## Impact: {module_name}"]

    node_type = attrs.get("type", attrs.get("kind", ""))
    file_path = attrs.get("file", attrs.get("path", ""))
    if file_path or node_type:
        parts = []
        if file_path:
            parts.append(f"Path: {file_path}")
        if node_type:
            parts.append(f"Type: {node_type}")
        lines.append(" | ".join(parts))

    deps = list(graph.successors(node_id))
    if deps:
        lines.append(f"\n**Dependencies** ({len(deps)}):")
        for dep in sorted(deps)[:15]:
            edge_data = graph.edges[node_id, dep]
            rel = edge_data.get("type", edge_data.get("relationship", ""))
            suffix = f" ({rel})" if rel else ""
            lines.append(f"- {dep}{suffix}")
        if len(deps) > 15:
            lines.append(f"- … and {len(deps) - 15} more")

    dependents = list(graph.predecessors(node_id))
    if dependents:
        lines.append(f"\n**Dependents** ({len(dependents)}):")
        for dep in sorted(dependents)[:15]:
            edge_data = graph.edges[dep, node_id]
            rel = edge_data.get("type", edge_data.get("relationship", ""))
            suffix = f" ({rel})" if rel else ""
            lines.append(f"- {dep}{suffix}")
        if len(dependents) > 15:
            lines.append(f"- … and {len(dependents) - 15} more")

    community = attrs.get("community", attrs.get("group", ""))
    if community:
        lines.append(f"\n**Community:** {community}")

    severity = "HIGH" if len(dependents) > 5 else "MEDIUM" if len(dependents) > 2 else "LOW"
    lines.append(f"\n**Change impact:** {severity} ({len(dependents)} direct dependents)")

    log.info("spec.impact.graph", module=module_name, deps=len(deps), dependents=len(dependents))
    return "\n".join(lines)


async def get_impact(module_name: str, project_path: Path) -> str:
    """Extract the subgraph centered on a named module from the repo spec.

    When the code knowledge graph is available, uses NetworkX traversal
    for instant, deterministic results. Falls back to agent-based extraction
    from SPEC.md when graph is unavailable.

    Returns a compact Markdown snippet sized for agent context inclusion.
    Raises FileNotFoundError if the spec file does not exist.
    """
    graph_result = _graph_impact(module_name, project_path)
    if graph_result is not None:
        return graph_result

    from factory.agents.runner import invoke_agent
    from factory.spec import read_spec

    spec_content = read_spec(project_path)

    prompt = IMPACT_PROMPT.format(
        module_name=module_name,
        spec_content=spec_content,
    )

    result, code = await invoke_agent(
        "researcher",
        prompt,
        project_path,
        timeout=120.0,
        dangerously_skip_permissions=True,
        model="haiku",
    )

    if code != 0:
        raise RuntimeError(f"Impact analysis agent failed (exit {code})")

    log.info("spec.impact", module=module_name)
    return result.strip()
