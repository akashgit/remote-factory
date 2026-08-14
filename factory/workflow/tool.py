"""Thin CLI adapters over a persisted interactive LangGraph workflow thread."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, TypedDict, cast

from factory.workflow.executor import ExecutionResult, WorkflowExecutor
from factory.workflow.langgraph import collect_subgraph_nodes
from factory.workflow.primitives import (
    AgentConfig,
    AgentNode,
    DEFAULT_AGENT_POOL,
    FnNode,
    ForkNode,
    GateNode,
    Study,
    SubgraphForkNode,
    VerdictType,
    Workflow,
)
from factory.workflow.registry import WorkflowRegistry
from factory.workflow.skill_export import _topological_sort


class ToolSession(TypedDict):
    """Metadata and exact DSL snapshot used to reconstruct one graph thread."""

    workflow_name: str
    thread_id: str
    original_project: str
    started_at: float
    workflow_json: str


_workflow_cache: dict[str, Workflow] = {}


def _resolve_original_project(worktree_path: Path) -> Path:
    """Resolve the original project path from a Factory worktree path."""
    parts = worktree_path.parts
    for index, part in enumerate(parts):
        if part == ".factory-worktrees":
            return Path(*parts[:index])
        if part == ".factory" and parts[index + 1:index + 2] == ("worktrees",):
            return Path(*parts[:index])
    return worktree_path


def _session_dir(project_path: Path) -> Path:
    return project_path / ".factory" / "langgraph"


def _session_path(project_path: Path) -> Path:
    return _session_dir(project_path) / "session.json"


def _load_session(project_path: Path) -> ToolSession:
    return cast(ToolSession, json.loads(_session_path(project_path).read_text()))


def _save_session(project_path: Path, session: ToolSession) -> None:
    path = _session_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, indent=2))


def _get_workflow_cached(name: str, project_path: Path) -> Workflow:
    """Load a workflow definition; graph checkpoints remain the execution authority."""
    cache_key = f"{project_path}:{name}"
    if cache_key in _workflow_cache:
        return _workflow_cache[cache_key]

    workflow = WorkflowRegistry.get_workflow(name, project_path)
    if workflow is None:
        raise ValueError(f"Workflow not found: {name}")
    _workflow_cache[cache_key] = workflow
    return workflow


def _executor(project_path: Path, session: ToolSession) -> WorkflowExecutor:
    cache_key = f"{project_path}:{session['workflow_name']}"
    workflow = _workflow_cache.get(cache_key)
    if workflow is None:
        workflow = Workflow.model_validate_json(session["workflow_json"])
        _workflow_cache[cache_key] = workflow
    return WorkflowExecutor(
        workflow,
        project_path,
        agent_pool=DEFAULT_AGENT_POOL,
        interactive=True,
        thread_id=session["thread_id"],
    )


def _inspect(project_path: Path) -> tuple[ToolSession, Workflow, ExecutionResult]:
    session = _load_session(project_path)
    executor = _executor(project_path, session)
    return session, executor.workflow, asyncio.run(executor.inspect())


def tool_init(
    workflow_name: str,
    project_path: Path,
    *,
    workflow: Workflow | None = None,
) -> str:
    """Start a persisted interactive workflow and pause at its first task."""
    workflow = workflow or WorkflowRegistry.get_workflow(workflow_name, project_path)
    if workflow is None:
        raise ValueError(f"Unknown workflow: {workflow_name}")

    thread_id = uuid.uuid4().hex[:12]
    session = ToolSession(
        workflow_name=workflow_name,
        thread_id=thread_id,
        original_project=str(_resolve_original_project(project_path)),
        started_at=time.time(),
        workflow_json=workflow.model_dump_json(),
    )
    _workflow_cache[f"{project_path}:{workflow_name}"] = workflow
    _save_session(project_path, session)
    asyncio.run(_executor(project_path, session).execute())
    return str(_session_dir(project_path))


def tool_next(project_path: Path, dry_run: bool = False) -> str:
    """Show pending graph tasks, auto-resuming tasks whose fresh artifacts exist."""
    session, workflow, result = _inspect(project_path)
    if dry_run:
        return _format_result(result, workflow, session, project_path)

    while result.interrupted:
        resumes: dict[str, str] = {}
        for pending in result.interrupts:
            payload = _interrupt_payload(pending)
            node = workflow.nodes[str(payload["node_id"])]
            artifact = _detect_artifact(
                node.id,
                node,
                project_path,
                session_start=session["started_at"],
            )
            if artifact is not None:
                resumes[str(pending["id"])] = artifact

        if not resumes:
            break
        result = asyncio.run(_executor(project_path, session).resume(resumes))

    return _format_result(result, workflow, session, project_path)


def tool_submit(project_path: Path, node_id: str, output: str) -> str:
    """Resume a pending graph interrupt for ``node_id`` with external output."""
    session, workflow, result = _inspect(project_path)
    matching = [
        pending
        for pending in result.interrupts
        if _interrupt_payload(pending)["node_id"] == node_id
    ]
    if not matching:
        pending_ids = [
            str(_interrupt_payload(pending)["node_id"])
            for pending in result.interrupts
        ]
        raise ValueError(
            f"node '{node_id}' is not pending; pending nodes: {', '.join(pending_ids) or 'none'}"
        )

    resumed = asyncio.run(
        _executor(project_path, session).resume({str(matching[0]["id"]): output})
    )
    if resumed.halted:
        return f"HALT\n{resumed.halt_reason}"
    if resumed.completed:
        return "DONE"

    reloop_target = _find_reloop_target(workflow, node_id)
    current_ids = {
        str(_interrupt_payload(pending)["node_id"])
        for pending in resumed.interrupts
    }
    if output.strip().startswith(("RELOOP", "RETRY")) and reloop_target in current_ids:
        count = int(resumed.state.get("iteration_counts", {}).get(
            f"{node_id}->{reloop_target}", 0,
        ))
        return f"RETRY\nRetry from: {reloop_target} (attempt {count}/3)"
    return "CONTINUE"


def tool_status(project_path: Path, fmt: str = "linear") -> str:
    """Show status projected from the current LangGraph checkpoint."""
    if not _session_path(project_path).exists():
        return "No active session. Run: factory workflow tool init <workflow> <project_path>"

    session, workflow, result = _inspect(project_path)
    state = _display_state(workflow, result)
    current_ids = _current_node_ids(result)
    current = ", ".join(current_ids) if current_ids else "DONE"
    lines = [
        f"Workflow: {session['workflow_name']}",
        f"Thread:   {session['thread_id']}",
        f"Status:   {state['status']}",
        f"Progress: {len(result.completed_nodes)}/{len(state['topo_order'])} nodes",
        f"Current:  {current}",
        "",
        _format_progress(
            state,
            workflow,
            project_path,
            set(current_ids),
            fmt=fmt,
        ),
    ]
    return "\n".join(lines)


def tool_finalize(project_path: Path) -> str:
    """Resume only pending tasks backed by fresh artifacts; never fake completion."""
    session, workflow, before = _inspect(project_path)
    result = before
    while result.interrupted:
        resumes: dict[str, str] = {}
        for pending in result.interrupts:
            payload = _interrupt_payload(pending)
            node = workflow.nodes[str(payload["node_id"])]
            artifact = _detect_artifact(
                node.id,
                node,
                project_path,
                session_start=session["started_at"],
            )
            if artifact is not None:
                resumes[str(pending["id"])] = artifact
        if not resumes:
            break
        result = asyncio.run(_executor(project_path, session).resume(resumes))

    advanced = len(result.completed_nodes) - len(before.completed_nodes)
    if result.halted:
        return f"HALT: {result.halt_reason}"
    if result.completed:
        return f"DONE. Finalized {advanced} node(s)."
    pending_text = ", ".join(_current_node_ids(result)) or "unknown"
    return f"Pending graph tasks: {pending_text}. Finalized {advanced} node(s)."


def tool_overview(project_path: Path, fmt: str = "linear") -> str:
    """Render the graph and checkpoint progress without advancing execution."""
    _, workflow, result = _inspect(project_path)
    return _format_progress(
        _display_state(workflow, result),
        workflow,
        project_path,
        set(_current_node_ids(result)),
        fmt=fmt,
    )


def tool_curr(project_path: Path) -> str:
    """Show pending graph task details without advancing execution."""
    session, workflow, result = _inspect(project_path)
    return _format_result(result, workflow, session, project_path)


def _interrupt_payload(interrupt: dict[str, Any]) -> dict[str, str]:
    value = interrupt["value"]
    if not isinstance(value, dict):
        raise TypeError(f"invalid workflow interrupt payload: {value!r}")
    return {"kind": str(value["kind"]), "node_id": str(value["node_id"])}


def _current_node_ids(result: ExecutionResult) -> list[str]:
    return [
        str(_interrupt_payload(pending)["node_id"])
        for pending in result.interrupts
    ]


def _display_order(workflow: Workflow) -> list[str]:
    nested: set[str] = set()
    for node in workflow.nodes.values():
        if isinstance(node, SubgraphForkNode):
            nested.update(
                collect_subgraph_nodes(
                    workflow,
                    node.subgraph_entry,
                    node.subgraph_exit,
                )
            )
    return [node_id for node_id in _topological_sort(workflow) if node_id not in nested]


def _display_state(workflow: Workflow, result: ExecutionResult) -> dict[str, Any]:
    if result.halted:
        status = "halted"
    elif result.completed:
        status = "completed"
    else:
        status = "active"
    return {
        **result.state,
        "topo_order": _display_order(workflow),
        "completed": {node_id: result.node_outputs.get(node_id, "") for node_id in result.completed_nodes},
        "status": status,
    }


def _format_result(
    result: ExecutionResult,
    workflow: Workflow,
    session: ToolSession,
    project_path: Path,
) -> str:
    if result.halted:
        return f"HALT\n{result.halt_reason}"
    if result.completed:
        return "DONE\nAll nodes completed."
    if not result.interrupted:
        return "PENDING\nWorkflow has no resumable task."

    state = _display_state(workflow, result)
    tasks: list[str] = []
    for pending in result.interrupts:
        payload = _interrupt_payload(pending)
        node_id = payload["node_id"]
        node = workflow.nodes[node_id]
        if payload["kind"] == "gate":
            if not isinstance(node, GateNode):
                raise TypeError(f"gate interrupt references {type(node).__name__}: {node_id}")
            if node.evaluator_type == "user":
                tasks.append(f"APPROVAL_NEEDED\n{node.gate_prompt}")
            else:
                tasks.append(
                    f"GATE\n{_format_gate_task(node_id, node, state, project_path)}"
                )
        else:
            tasks.append(_format_node_task(node_id, node, workflow, state, project_path))

    if len(tasks) == 1:
        return tasks[0]
    return "PARALLEL\n" + "\n\n---\n\n".join(tasks)


def _phase_label(node_id: str, node: object) -> str:
    """Generate a human-readable phase label from node id and type."""
    name = node_id.replace("_", " ").title()
    if isinstance(node, AgentNode):
        role = node.role.value.replace("_", " ").title()
        return role if role.lower() in name.lower() else f"{role} — {name}"
    if isinstance(node, GateNode):
        gate_name = node_id.replace("gate_", "").replace("_", " ").title()
        return f"Gate — {gate_name}"
    if isinstance(node, Study):
        return f"Observe ({node_id})"
    if isinstance(node, ForkNode):
        return f"Fork ({', '.join(node.targets)})"
    if isinstance(node, FnNode):
        return name
    return name


def _format_progress(
    state: dict[str, Any],
    workflow: Workflow | None,
    project_path: Path,
    current_nodes: str | set[str] | None,
    fmt: str = "linear",
) -> str:
    """Build a progress view of the workflow with completion markers."""
    current = {current_nodes} if isinstance(current_nodes, str) else current_nodes or set()
    order = state["topo_order"]
    completed = state["completed"]
    lines: list[str] = []
    for index, node_id in enumerate(order):
        node = workflow.nodes.get(node_id) if workflow else None
        is_current = node_id in current
        is_done = node_id in completed
        marker = "✓" if is_done else "▶" if is_current else "○"
        if fmt == "phased":
            label = _phase_label(node_id, node) if node else node_id.replace("_", " ").title()
            line = f"{marker} Phase {index + 1}: {label}"
        else:
            line = f"{marker} {node_id}"
        if is_current:
            line += "    ← CURRENT"
        lines.append(line)
    return "\n".join(lines)


def _find_loop_context(
    node_id: str,
    workflow: Workflow,
    state: dict[str, Any],
    project_path: Path,
) -> str:
    """Build loop topology and persisted LangGraph feedback for a reloop target."""
    reloop_edges = [
        edge
        for edge in workflow.edges
        if edge.target == node_id and edge.condition == VerdictType.RELOOP
    ]
    if not reloop_edges:
        return ""

    order = state.get("topo_order", [])
    iteration_counts = state.get("iteration_counts", {})
    feedback_log = state.get("feedback_log", {})
    entries = feedback_log.get(node_id, [])
    latest = max(entries, key=lambda entry: entry.get("timestamp", 0)) if entries else None
    active_edge = next(
        (edge for edge in reloop_edges if latest and edge.source == latest.get("gate")),
        reloop_edges[0],
    )
    gate_id = active_edge.source
    count = int(iteration_counts.get(f"{gate_id}->{node_id}", 0))
    max_iterations = 3
    lines = ["", "## LOOP CONTEXT", f"Iteration: {count}/{max_iterations}"]
    if count >= max_iterations:
        lines.append("⚠ FINAL ATTEMPT — this is the last iteration before HALT")

    gate_node = workflow.nodes.get(gate_id)
    lines.append(f"Triggered by: {gate_id}")
    if isinstance(gate_node, GateNode):
        if gate_node.gate_prompt:
            prompt = gate_node.gate_prompt.replace("{project_path}", str(project_path))
            lines.append(f"Gate criteria: {prompt}")
        if gate_node.evaluator_command:
            command = gate_node.evaluator_command.replace("{project_path}", str(project_path))
            lines.append(f"Gate command: {command}")

    if node_id in order and gate_id in order:
        node_index = order.index(node_id)
        gate_index = order.index(gate_id)
        if node_index < gate_index:
            lines.extend(["", "### Loop topology"])
            for loop_id in order[node_index:gate_index + 1]:
                loop_node = workflow.nodes.get(loop_id)
                parts = [f"- **{loop_id}**"]
                if isinstance(loop_node, AgentNode):
                    parts.append(f"(agent: {loop_node.role.value})")
                    if loop_node.reads:
                        parts.append(f"reads: {', '.join(sorted(loop_node.reads))}")
                    if loop_node.writes:
                        parts.append(f"writes: {', '.join(sorted(loop_node.writes))}")
                elif isinstance(loop_node, GateNode):
                    parts.append(f"(gate: {loop_node.evaluator_type})")
                elif isinstance(loop_node, FnNode):
                    parts.append("(fn)")
                lines.append(" ".join(parts))

    if entries:
        lines.extend(["", "### Feedback history"])
        for entry in sorted(entries, key=lambda item: item.get("timestamp", 0))[-2:]:
            feedback = str(entry.get("feedback", ""))[:500]
            lines.append(
                f"- [{entry.get('gate', '?')} iter {entry.get('iteration', '?')}] {feedback}"
            )
    return "\n".join(lines)


def _format_node_task(
    node_id: str,
    node: object,
    workflow: Workflow,
    state: dict[str, Any],
    project_path: Path,
) -> str:
    """Format one pending graph node as a human-readable task."""
    lines = [f"Node: {node_id}"]
    if isinstance(node, AgentNode):
        role = node.role.value
        pool_config: AgentConfig | None = DEFAULT_AGENT_POOL.get(role)
        model = node.model or (pool_config.model if pool_config else "opus")
        timeout = node.timeout or (pool_config.timeout if pool_config else 600)
        lines.extend([
            f"Type: Agent ({role})",
            f"Model: {model}",
            f"Timeout: {timeout}s",
        ])
        if node.prompt_template:
            task = node.prompt_template.replace("{project_path}", str(project_path))
            lines.append(f"Task: {task}")
        if node.reads:
            lines.append(f"Reads: {', '.join(sorted(node.reads))}")
        if node.writes:
            lines.append(f"Writes: {', '.join(sorted(node.writes))}")
    elif isinstance(node, GateNode):
        lines.append(f"Type: Gate ({node.evaluator_type})")
        if node.gate_prompt:
            lines.append(f"Evaluate: {node.gate_prompt}")
        if node.evaluator_command:
            command = node.evaluator_command.replace("{project_path}", str(project_path))
            lines.append(f"Command: {command}")
        if node.reads:
            lines.append(f"Reads: {', '.join(sorted(node.reads))}")
    elif isinstance(node, Study):
        command = node.command.replace("{project_path}", str(project_path))
        lines.extend(["Type: Study", f"Command: {command}"])
    elif isinstance(node, FnNode):
        command = node.command.replace("{project_path}", str(project_path))
        lines.extend(["Type: Function", f"Command: {command}"])
        if node.notes:
            lines.append(f"Notes: {node.notes}")
    elif isinstance(node, ForkNode):
        lines.extend([
            "Type: Fork",
            f"Targets: {', '.join(node.targets)}",
            "Execute all targets (listed as parallel pending nodes).",
        ])

    loop_context = _find_loop_context(node_id, workflow, state, project_path)
    if loop_context:
        lines.append(loop_context)
    return "\n".join(lines)


def _format_gate_task(
    node_id: str,
    gate_node: GateNode,
    state: dict[str, Any],
    project_path: Path,
) -> str:
    """Format one pending gate interrupt as a review task."""
    prompt = gate_node.gate_prompt or "Review the output of the preceding step."
    prompt = prompt.replace("{project_path}", str(project_path))
    reads = ", ".join(sorted(gate_node.reads)) if gate_node.reads else "none"
    workflow = _get_workflow_cached(str(state["workflow_name"]), project_path)
    reloop_targets = [
        edge.target
        for edge in workflow.edges
        if edge.source == node_id and edge.condition == VerdictType.RELOOP
    ]
    return "\n".join([
        f"Gate: {node_id}",
        f"Review: {prompt}",
        f"Read: {reads}",
        f"Reloop targets: {reloop_targets if reloop_targets else 'none'}",
        "",
        "Respond with one of:",
        "  PROCEED",
        '  RETRY target=<node_id> feedback="<feedback>"',
        '  HALT reason="<reason>"',
    ])


def _detect_artifact(
    node_id: str,
    node: object,
    project_path: Path,
    session_start: float = 0.0,
) -> str | None:
    """Return a fresh external artifact for a pending graph node, if present."""
    reviews_dir = project_path / ".factory" / "reviews"

    def fresh(path: Path) -> bool:
        return session_start <= 0 or path.stat().st_mtime >= session_start

    if isinstance(node, AgentNode):
        role = node.role.value
        tag = node_id.replace(f"{role}_", "").replace(role, "")
        candidates: list[Path] = []
        if tag and tag != node_id:
            candidates.append(reviews_dir / f"{role}-{tag}-latest.md")
        candidates.append(reviews_dir / f"{role}-latest.md")
        candidates.extend(project_path / path for path in sorted(node.writes))
        for candidate in candidates:
            if candidate.exists() and fresh(candidate):
                content = candidate.read_text().strip()
                if content:
                    return content
        return None

    if isinstance(node, Study):
        observation = project_path / ".factory" / "strategy" / "observations.md"
        if observation.exists() and fresh(observation):
            content = observation.read_text().strip()
            return content if len(content) > 50 else None
        return None

    if isinstance(node, FnNode):
        outputs = [project_path / path for path in sorted(node.writes)]
        if outputs and all(path.exists() and fresh(path) for path in outputs):
            return "; ".join(path.read_text().strip()[:500] for path in outputs)
        return None

    if isinstance(node, ForkNode):
        return json.dumps({"targets": node.targets})
    return None


def _find_reloop_target(workflow: Workflow, gate_id: str) -> str | None:
    """Find the RELOOP target for a gate node."""
    for edge in workflow.edges:
        if edge.source == gate_id and edge.condition == VerdictType.RELOOP:
            return edge.target
    return None
