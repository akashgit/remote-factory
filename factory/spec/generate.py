"""Spec generation orchestration — graphify extraction + single annotator agent."""

from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger()


def _build_annotate_prompt(project_path: Path) -> str:
    """Build the annotator agent prompt for producing SPEC.md.

    All format, section, and graph reference instructions live in
    factory/agents/prompts/spec_annotator.md — this prompt just points the
    agent at the template and the graph data.
    """
    graph_path = project_path / "graph.json"
    return (
        f"Generate a behavioral overview spec for the project at {project_path}.\n\n"
        f"Read the spec_annotator prompt at factory/agents/prompts/spec_annotator.md "
        f"and follow it exactly — it defines the output format, required sections, "
        f"graph reference link syntax, and completeness checklist.\n\n"
        f"Read the code knowledge graph at {graph_path}.\n\n"
        f"Write the annotated repo spec to {project_path / 'SPEC.md'}."
    )


async def generate_spec(project_path: Path) -> Path:
    """Generate a repo spec for a project.

    1. Run graphify extract → graph.json (local AST, no LLM cost)
    2. Annotator agent reads graph.json directly → produces SPEC.md

    Returns the path to the generated SPEC.md.
    Raises RuntimeError if graphify is not installed or extraction fails.
    """
    from factory.agents.runner import invoke_agent
    from factory.graph import extract_graph, is_graphify_installed

    if not is_graphify_installed():
        raise RuntimeError(
            "graphify is required for spec generation. Install with: uv tool install graphifyy"
        )

    factory_dir = project_path / ".factory"
    factory_dir.mkdir(parents=True, exist_ok=True)

    graph_path = extract_graph(project_path)
    if graph_path is None:
        raise RuntimeError("graphify extraction failed — check logs for details")

    log.info("spec.generate.graph", graph_path=str(graph_path))

    annotate_task = _build_annotate_prompt(project_path)

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
