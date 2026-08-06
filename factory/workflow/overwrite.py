"""Runtime workflow mutation via natural-language overwrite directives.

The overwrite pipeline: parse directive -> strategist interprets as JSON
mutations -> apply to Workflow -> validate -> generate session-local SKILL.md.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import structlog

from factory.workflow.primitives import Edge, Workflow

log = structlog.get_logger()


def apply_overwrite(
    workflow: Workflow,
    overwrite_text: str,
    project_path: Path,
) -> Workflow:
    """Interpret a natural-language overwrite and apply it to a workflow.

    Returns the mutated workflow. Raises on validation failure.
    """
    log.info("overwrite.start", workflow=workflow.name, text=overwrite_text[:80])
    mutations = _interpret_overwrite(workflow, overwrite_text, project_path)
    mutated = _apply_mutations(workflow, mutations)
    issues = mutated.validate_graph()
    if issues:
        raise ValueError(f"Mutated workflow has validation errors: {issues}")
    log.info("overwrite.done", mutations=len(mutations))
    return mutated


def _interpret_overwrite(
    workflow: Workflow,
    overwrite_text: str,
    project_path: Path,
) -> list[dict]:
    """Call headless strategist to interpret overwrite text as structured mutations."""
    import asyncio

    from factory.agents.runner import invoke_agent

    node_summary = json.dumps(
        {nid: {"type": type(n).__name__, "fields": list(type(n).model_fields.keys())}
         for nid, n in workflow.nodes.items()},
        indent=2,
    )
    edge_summary = json.dumps(
        [{"source": e.source, "target": e.target, "condition": e.condition.value if e.condition else None}
         for e in workflow.edges],
        indent=2,
    )

    task = f"""You are interpreting a workflow overwrite directive.

## Current workflow: {workflow.name}

### Nodes
{node_summary}

### Edges
{edge_summary}

## Overwrite directive
{overwrite_text}

## Instructions
Return ONLY a JSON array of mutation operations. No markdown, no explanation.
Each mutation is one of:

- {{"op": "update_node", "node_id": "<id>", "field": "<field_name>", "value": "<new_value>"}}
- {{"op": "remove_node", "node_id": "<id>"}}
- {{"op": "add_edge", "source": "<node_id>", "target": "<node_id>"}}
- {{"op": "remove_edge", "source": "<node_id>", "target": "<node_id>"}}

For update_node, valid fields depend on the node type (e.g. prompt_template, timeout, model for AgentNode).
Return the minimal set of mutations that implements the directive."""

    stdout, _code = asyncio.run(invoke_agent(
        role="strategist",
        task=task,
        project_path=project_path,
        timeout=120,
        model="sonnet",
    ))
    return _parse_mutations(stdout)


def _parse_mutations(raw: str) -> list[dict]:
    """Extract JSON mutation array from agent output."""
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON array found in strategist output: {raw[:200]}")
    return json.loads(raw[start : end + 1])


def _apply_mutations(workflow: Workflow, mutations: list[dict]) -> Workflow:
    """Apply a list of mutations to a workflow, returning a new Workflow."""
    nodes = {nid: n.model_copy(deep=True) for nid, n in workflow.nodes.items()}
    edges = [e.model_copy(deep=True) for e in workflow.edges]

    for mut in mutations:
        op = mut["op"]

        if op == "update_node":
            node_id = mut["node_id"]
            if node_id not in nodes:
                raise KeyError(f"Node '{node_id}' not found in workflow")
            field = mut["field"]
            value = mut["value"]
            node = nodes[node_id]
            if field not in type(node).model_fields:
                raise KeyError(f"Field '{field}' not found on node '{node_id}' ({type(node).__name__})")
            updated = node.model_copy(update={field: value})
            nodes[node_id] = updated

        elif op == "remove_node":
            node_id = mut["node_id"]
            if node_id not in nodes:
                raise KeyError(f"Node '{node_id}' not found in workflow")
            del nodes[node_id]
            edges = [e for e in edges if e.source != node_id and e.target != node_id]

        elif op == "add_edge":
            src, tgt = mut["source"], mut["target"]
            edges.append(Edge(source=src, target=tgt))

        elif op == "remove_edge":
            src, tgt = mut["source"], mut["target"]
            before = len(edges)
            edges = [e for e in edges if not (e.source == src and e.target == tgt)]
            if len(edges) == before:
                log.warning("overwrite.edge_not_found", source=src, target=tgt)

        else:
            raise ValueError(f"Unknown mutation op: {op}")

    start_node = workflow.start_node if workflow.start_node in nodes else next(iter(nodes))
    return Workflow(
        name=workflow.name,
        nodes=nodes,
        edges=edges,
        start_node=start_node,
        terminal=workflow.terminal,
        trigger=workflow.trigger,
    )


def generate_session_skill(
    workflow: Workflow,
    mode: str,
    wt_path: Path,
) -> Path:
    """Generate SKILL.md from a mutated workflow into the worktree's skills/ dir."""
    from factory.workflow.skill_export import export_all_skills

    with tempfile.TemporaryDirectory(prefix="factory-overwrite-") as tmp:
        tmp_path = Path(tmp)
        export_all_skills(tmp_path, {mode: workflow})
        src_dir = tmp_path / f"workflow-{mode}"
        if not src_dir.exists():
            raise FileNotFoundError(f"Expected skill dir {src_dir} not generated")
        dst_dir = wt_path / "skills" / f"workflow-{mode}"
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        skill_md = dst_dir / "SKILL.md"
        log.info("overwrite.skill_generated", path=str(skill_md))
        return skill_md
