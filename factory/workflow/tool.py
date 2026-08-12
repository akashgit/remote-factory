"""Tool-based workflow execution — step-by-step cursor over the DAG."""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path

import structlog

from factory.workflow.primitives import (
    AgentConfig,
    AgentNode,
    DEFAULT_AGENT_POOL,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    Study,
    VerdictType,
    Workflow,
    _role_str,
)
from factory.workflow.registry import WorkflowRegistry
from factory.workflow.skill_export import _topological_sort

log = structlog.get_logger()

_workflow_cache: dict[str, Workflow] = {}


def _resolve_original_project(wt_path: Path) -> Path:
    """Resolve the original project path from a worktree path.

    Worktree paths look like: /project/.factory-worktrees/run-xxx
    or: /project/.factory/worktrees/run-xxx
    Falls back to wt_path itself if not a worktree.
    """
    parts = wt_path.parts
    for i, part in enumerate(parts):
        if part == ".factory-worktrees":
            return Path(*parts[:i])
        if part == ".factory" and i + 1 < len(parts) and parts[i + 1] == "worktrees":
            return Path(*parts[:i])
    return wt_path


def _load_state(project_path: Path) -> dict:
    state_path = project_path / ".factory" / "tool_session" / "state.json"
    return json.loads(state_path.read_text())


def _save_state(project_path: Path, state: dict) -> None:
    state_path = project_path / ".factory" / "tool_session" / "state.json"
    state_path.write_text(json.dumps(state, indent=2))


def _emit_event(project_path: Path, event_type: str, **data: object) -> None:
    """Append a structured event to .factory/events.jsonl.

    Resolves the original project path so events survive worktree deletion.
    """
    try:
        state_path = project_path / ".factory" / "tool_session" / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())
            orig = state.get("original_project")
            if orig:
                target = Path(orig)
            else:
                target = _resolve_original_project(project_path)
        else:
            target = _resolve_original_project(project_path)
    except Exception:
        target = project_path

    event = {
        "type": event_type,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **data,
    }
    events_file = target / ".factory" / "events.jsonl"
    events_file.parent.mkdir(parents=True, exist_ok=True)
    with open(events_file, "a") as f:
        f.write(json.dumps(event) + "\n")


def _rebuild_workflow(cache_data: dict) -> Workflow:
    """Rebuild a Workflow from cached JSON data."""
    from factory.workflow.primitives import AgentRole, Edge, VerdictType

    from factory.workflow.primitives import NodeType

    def _parse_role(raw: str) -> AgentRole | str:
        try:
            return AgentRole(raw)
        except ValueError:
            return raw

    nodes: dict[str, NodeType] = {}
    for nid, info in cache_data["nodes"].items():
        ntype = info["type"]
        common: dict[str, object] = {
            "id": nid,
            "reads": set(info.get("reads", [])),
            "writes": set(info.get("writes", [])),
            "blocking": info.get("blocking", True),
        }

        if ntype == "AgentNode":
            nodes[nid] = AgentNode(
                **common,  # type: ignore[arg-type]
                role=_parse_role(info["role"]),
                model=info.get("model", ""),
                prompt_template=info.get("prompt_template", ""),
                timeout=info.get("timeout"),
                max_iterations=info.get("max_iterations", 1),
            )
        elif ntype == "GateNode":
            nodes[nid] = GateNode(
                **common,  # type: ignore[arg-type]
                evaluator_type=info.get("evaluator_type", "agent"),
                evaluator_command=info.get("evaluator_command"),
                gate_prompt=info.get("gate_prompt", ""),
                evaluator_role=_parse_role(info["evaluator_role"]) if info.get("evaluator_role") else None,
            )
        elif ntype == "Study":
            nodes[nid] = Study(
                **common,  # type: ignore[arg-type]
                command=info.get("command", ""),
                focus=info.get("focus"),
            )
        elif ntype == "FnNode":
            nodes[nid] = FnNode(
                **common,  # type: ignore[arg-type]
                command=info.get("command", ""),
                notes=info.get("notes", ""),
            )
        elif ntype == "ForkNode":
            nodes[nid] = ForkNode(
                **common,  # type: ignore[arg-type]
                targets=info.get("targets", []),
            )
        elif ntype == "JoinNode":
            nodes[nid] = JoinNode(
                **common,  # type: ignore[arg-type]
                sources=info.get("sources", []),
            )
        else:
            nodes[nid] = FnNode(**common, command="", notes="")  # type: ignore[arg-type]

    edges = []
    for e in cache_data.get("edges", []):
        edges.append(Edge(
            source=e["source"],
            target=e["target"],
            condition=VerdictType(e["condition"]) if e.get("condition") else None,
        ))

    return Workflow(
        name=cache_data["name"],
        nodes=nodes,
        edges=edges,
        start_node=cache_data["start_node"],
    )


def _get_workflow_cached(name: str, project_path: Path) -> Workflow:
    cache_key = f"{project_path}:{name}"
    if cache_key in _workflow_cache:
        return _workflow_cache[cache_key]

    cache_file = project_path / ".factory" / "tool_session" / "workflow_cache.json"
    if cache_file.exists():
        try:
            cache_data = json.loads(cache_file.read_text())
            if cache_data.get("name") == name:
                wf = _rebuild_workflow(cache_data)
                _workflow_cache[cache_key] = wf
                return wf
        except Exception:
            pass

    from factory.workflow.definitions import register_all

    all_wf = register_all()
    found: Workflow | None = all_wf.get(name)
    if not found:
        found = WorkflowRegistry.get_workflow(name, project_path)
    if not found:
        raise ValueError(f"Workflow not found: {name}")
    _workflow_cache[cache_key] = found
    return found


def tool_init(workflow_name: str, project_path: Path) -> str:
    """Initialize a tool session. Returns session dir path."""
    wf = WorkflowRegistry.get_workflow(workflow_name, project_path)
    if not wf:
        raise ValueError(f"Unknown workflow: {workflow_name}")

    session_dir = project_path / ".factory" / "tool_session"
    session_dir.mkdir(parents=True, exist_ok=True)

    order = _topological_sort(wf)

    order = [nid for nid in order if not isinstance(wf.nodes.get(nid), JoinNode)]

    state = {
        "workflow_name": workflow_name,
        "session_id": uuid.uuid4().hex[:12],
        "original_project": str(_resolve_original_project(project_path)),
        "started_at": int(time.time()),
        "topo_order": order,
        "pointer_idx": 0,
        "completed": {},
        "gate_results": {},
        "iteration_counts": {},
        "feedback_log": {},
        "status": "active",
    }

    (session_dir / "state.json").write_text(json.dumps(state, indent=2))

    cache_data: dict[str, object] = {
        "name": wf.name,
        "start_node": wf.start_node,
        "nodes": {},
        "edges": [
            {
                "source": e.source,
                "target": e.target,
                "condition": e.condition.value if e.condition else None,
            }
            for e in wf.edges
        ],
    }
    nodes_cache: dict[str, dict[str, object]] = {}
    for nid, node in wf.nodes.items():
        node_info: dict[str, object] = {
            "type": type(node).__name__,
            "id": nid,
            "blocking": node.blocking,
            "reads": sorted(node.reads),
            "writes": sorted(node.writes),
        }
        if isinstance(node, AgentNode):
            node_info["role"] = _role_str(node.role)
            node_info["model"] = node.model
            node_info["prompt_template"] = node.prompt_template
            node_info["timeout"] = node.timeout
            node_info["max_iterations"] = node.max_iterations
        elif isinstance(node, GateNode):
            node_info["evaluator_type"] = node.evaluator_type
            node_info["evaluator_command"] = node.evaluator_command
            node_info["gate_prompt"] = node.gate_prompt
            if node.evaluator_role:
                node_info["evaluator_role"] = _role_str(node.evaluator_role)
        elif isinstance(node, Study):
            node_info["command"] = node.command
            node_info["focus"] = node.focus
        elif isinstance(node, FnNode):
            node_info["command"] = node.command
            node_info["notes"] = node.notes
        elif isinstance(node, ForkNode):
            node_info["targets"] = node.targets
        nodes_cache[nid] = node_info
    cache_data["nodes"] = nodes_cache
    (session_dir / "workflow_cache.json").write_text(json.dumps(cache_data, indent=2))

    _emit_event(
        project_path, "workflow.tool.init",
        workflow=workflow_name, session_id=state["session_id"], nodes=len(order),
    )
    return str(session_dir)


def tool_next(project_path: Path, dry_run: bool = False) -> str:
    """Get the next node to execute.

    Auto-submits any pending node whose artifacts exist:
    - AgentNode: .factory/reviews/<role>-latest.md or <role>-<tag>-latest.md
    - Study: .factory/strategy/observations.md
    - FnNode: declared writes exist
    - ForkNode: skip (handled by sequential ordering)

    The CEO never calls submit for agent/fn nodes — just next repeatedly.
    Submit is only needed for gate verdicts.

    When dry_run=True, runs the auto-submit scan but does not persist state
    changes or emit events.
    """
    import copy

    state = _load_state(project_path)
    if dry_run:
        state = copy.deepcopy(state)

    if state["status"] != "active":
        if not dry_run:
            finalize_msg = tool_finalize(project_path)
            return f"DONE\n{finalize_msg}"
        return "DONE"

    wf = _get_workflow_cached(state["workflow_name"], project_path)
    order = state["topo_order"]
    idx = state["pointer_idx"]

    while idx < len(order):
        nid = order[idx]

        if nid in state["completed"]:
            idx += 1
            continue

        node = wf.nodes[nid]
        artifact = _detect_artifact(
            nid, node, project_path, session_start=state.get("started_at", 0.0),
        )

        if artifact is not None:
            state["completed"][nid] = artifact
            if isinstance(node, AgentNode) and node.writes:
                for wp in node.writes:
                    out = project_path / wp
                    out.parent.mkdir(parents=True, exist_ok=True)
                    if not out.exists():
                        out.write_text(artifact)
            log.info("tool.auto_submit", node=nid)
            if not dry_run:
                _emit_event(project_path, "workflow.tool.auto_submit", node=nid)
            idx += 1
            state["pointer_idx"] = idx

            if idx < len(order):
                next_nid = order[idx]
                next_node = wf.nodes.get(next_nid)
                if (
                    isinstance(next_node, GateNode)
                    and next_node.evaluator_type == "fn"
                    and next_node.evaluator_command
                ):
                    gate_result = _auto_evaluate_fn_gate(
                        next_node, project_path, state, wf, order, idx,
                    )
                    if gate_result:
                        return gate_result
                    idx = state["pointer_idx"]

            if not dry_run:
                _save_state(project_path, state)
            continue

        break

    state["pointer_idx"] = idx
    if not dry_run:
        _save_state(project_path, state)

    if idx >= len(order):
        if not dry_run:
            finalize_msg = tool_finalize(project_path)
            return f"DONE\n{finalize_msg}"
        return "DONE"

    nid = order[idx]
    node = wf.nodes[nid]

    if not dry_run:
        _emit_event(project_path, "workflow.tool.next", node=nid, node_type=type(node).__name__)

    if isinstance(node, GateNode) and node.evaluator_type == "agent":
        return f"GATE\n{_format_gate_task(nid, node, state, project_path)}"

    if isinstance(node, GateNode) and node.evaluator_type == "user":
        return f"APPROVAL_NEEDED\n{node.gate_prompt}"

    result = _format_node_task(nid, node, wf, state, project_path)

    loop_ctx = _find_loop_context(nid, wf, order, state, project_path)
    if loop_ctx:
        result += f"\n\n{loop_ctx}"

    return result


def tool_submit(project_path: Path, node_id: str, output: str) -> str:
    """Submit output for a node (primarily used for gate verdicts)."""
    state = _load_state(project_path)
    wf = _get_workflow_cached(state["workflow_name"], project_path)

    state["completed"][node_id] = output
    _emit_event(project_path, "workflow.tool.submit", node=node_id)

    if isinstance(wf.nodes.get(node_id), GateNode) and output.strip().startswith("RETRY"):
        import re as _re
        target_m = _re.search(r'target=(\S+)', output)
        feedback_m = _re.search(r'feedback="([^"]*)"', output)
        if target_m:
            reloop_target = target_m.group(1)
            feedback_text = feedback_m.group(1) if feedback_m else output[:500]
            feedback_log = state.setdefault("feedback_log", {})
            entries = feedback_log.setdefault(reloop_target, [])
            entries.append({
                "gate": node_id,
                "iteration": len([e for e in entries if e["gate"] == node_id]) + 1,
                "feedback": feedback_text[:500],
                "timestamp": time.time(),
            })

    node = wf.nodes.get(node_id)
    if isinstance(node, AgentNode) and node.writes:
        for write_path in node.writes:
            out_file = project_path / write_path
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(output)

    order = state["topo_order"]
    idx = state["pointer_idx"]

    if idx < len(order) and order[idx] == node_id:
        idx += 1

    state["pointer_idx"] = idx

    if idx >= len(order):
        state["status"] = "completed"
        _save_state(project_path, state)
        return "DONE"

    next_nid = order[idx]
    next_node = wf.nodes.get(next_nid)
    if (
        isinstance(next_node, GateNode)
        and next_node.evaluator_type == "fn"
        and next_node.evaluator_command
    ):
        gate_result = _auto_evaluate_fn_gate(
            next_node, project_path, state, wf, order, idx,
        )
        if gate_result:
            return gate_result

    _save_state(project_path, state)
    return "CONTINUE"


def tool_status(project_path: Path, fmt: str = "linear") -> str:
    """Get current session status."""
    state_path = project_path / ".factory" / "tool_session" / "state.json"
    if not state_path.exists():
        return "No active session. Run: factory tool init <workflow> <project_path>"

    state = json.loads(state_path.read_text())
    order = state["topo_order"]
    idx = state["pointer_idx"]
    current = order[idx] if idx < len(order) else "DONE"
    completed_count = len(state["completed"])
    total = len(order)

    try:
        wf = _get_workflow_cached(state["workflow_name"], project_path)
    except Exception:
        wf = None

    current_nid = current if current != "DONE" else None

    lines = [
        f"Workflow: {state['workflow_name']}",
        f"Session:  {state['session_id']}",
        f"Status:   {state['status']}",
        f"Progress: {completed_count}/{total} nodes",
        f"Current:  {current}",
    ]

    if state["gate_results"]:
        lines.append(f"Gates:    {json.dumps(state['gate_results'])}")

    lines.append("")
    lines.append(_format_progress(state, wf, project_path, current_nid, fmt=fmt))

    return "\n".join(lines)


def tool_finalize(project_path: Path) -> str:
    """Finalize the tool session — mark any remaining untracked nodes as complete.

    Scans forward from the current pointer, auto-completing any nodes whose
    artifacts exist but weren't tracked (e.g., async agents like archivist).
    """
    state = _load_state(project_path)
    wf = _get_workflow_cached(state["workflow_name"], project_path)
    order = state["topo_order"]

    finalized = []
    for nid in order:
        if nid in state["completed"]:
            continue
        node = wf.nodes[nid]
        artifact = _detect_artifact(
            nid, node, project_path, session_start=state.get("started_at", 0.0),
        )
        if artifact is not None:
            state["completed"][nid] = artifact
            finalized.append(nid)
            log.info("tool.finalize", node=nid)

    if len(state["completed"]) >= len(order):
        state["status"] = "completed"

    state["pointer_idx"] = len(order)
    _save_state(project_path, state)

    _emit_event(project_path, "workflow.tool.finalize", nodes=finalized)

    if finalized:
        return (
            f"Finalized {len(finalized)} node(s): {', '.join(finalized)}\n"
            f"Progress: {len(state['completed'])}/{len(order)}"
        )
    return f"No pending nodes to finalize. Progress: {len(state['completed'])}/{len(order)}"


def tool_overview(project_path: Path, fmt: str = "linear") -> str:
    """Render the full workflow map with completion markers. Does NOT advance."""
    state = _load_state(project_path)
    wf = _get_workflow_cached(state["workflow_name"], project_path)
    order = state["topo_order"]
    idx = state["pointer_idx"]
    current_nid = order[idx] if idx < len(order) else None
    return _format_progress(state, wf, project_path, current_nid, fmt=fmt)


def tool_curr(project_path: Path) -> str:
    """Show current node details without advancing or auto-submitting."""
    state = _load_state(project_path)
    wf = _get_workflow_cached(state["workflow_name"], project_path)
    order = state["topo_order"]
    idx = state["pointer_idx"]

    if idx >= len(order):
        return "DONE\nAll nodes completed."

    nid = order[idx]
    node = wf.nodes[nid]
    return _format_node_task(nid, node, wf, state, project_path)


# ── helpers ─────────────────────────────────────────────────────


def _phase_label(nid: str, node: object) -> str:
    """Generate a human-readable phase label from node id and type."""
    name = nid.replace("_", " ").title()

    if isinstance(node, AgentNode):
        role = _role_str(node.role).replace("_", " ").title()
        return role if role.lower() in name.lower() else f"{role} — {name}"
    elif isinstance(node, GateNode):
        gate_name = nid.replace("gate_", "").replace("_", " ").title()
        return f"Gate — {gate_name}"
    elif isinstance(node, Study):
        return f"Observe ({nid})"
    elif isinstance(node, ForkNode):
        return f"Fork ({', '.join(node.targets)})"
    elif isinstance(node, FnNode):
        return name
    return name


def _format_progress(
    state: dict,
    wf: Workflow | None,
    project_path: Path,
    current_nid: str | None,
    fmt: str = "linear",
) -> str:
    """Build a progress view of the workflow with completion markers."""
    order = state["topo_order"]
    completed = state["completed"]
    lines: list[str] = []

    for i, nid in enumerate(order):
        node = wf.nodes.get(nid) if wf else None
        is_current = nid == current_nid
        is_done = nid in completed

        if is_done:
            marker = "✓"
        elif is_current:
            marker = "▶"
        else:
            marker = "○"

        if fmt == "phased":
            label = _phase_label(nid, node) if node else nid.replace("_", " ").title()
            line = f"{marker} Phase {i + 1}: {label}"
        else:
            line = f"{marker} {nid}"

        if is_current:
            line += "    ← CURRENT"

        lines.append(line)

        if is_current and node is not None and wf is not None:
            details = _format_node_task(nid, node, wf, state, project_path)
            for detail_line in details.split("\n"):
                if detail_line.startswith("Node:"):
                    continue
                lines.append(f"  {detail_line}")

    return "\n".join(lines)


def _format_node_task(
    nid: str, node: object, wf: Workflow, state: dict, project_path: Path,
) -> str:
    """Format a node as a human-readable task description."""
    lines = [f"Node: {nid}"]

    if isinstance(node, AgentNode):
        role = _role_str(node.role)
        pool_cfg: AgentConfig | None = DEFAULT_AGENT_POOL.get(role)
        model = node.model or (pool_cfg.model if pool_cfg else "opus")
        timeout = node.timeout or (pool_cfg.timeout if pool_cfg else 600)

        lines.append(f"Type: Agent ({role})")
        lines.append(f"Model: {model}")
        lines.append(f"Timeout: {timeout}s")

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
            cmd = node.evaluator_command.replace("{project_path}", str(project_path))
            lines.append(f"Command: {cmd}")
        if node.reads:
            lines.append(f"Reads: {', '.join(sorted(node.reads))}")

    elif isinstance(node, Study):
        cmd = node.command.replace("{project_path}", str(project_path))
        lines.append("Type: Study")
        lines.append(f"Command: {cmd}")

    elif isinstance(node, FnNode):
        cmd = node.command.replace("{project_path}", str(project_path))
        lines.append("Type: Function")
        lines.append(f"Command: {cmd}")
        if node.notes:
            lines.append(f"Notes: {node.notes}")

    elif isinstance(node, ForkNode):
        lines.append("Type: Fork")
        lines.append(f"Targets: {', '.join(node.targets)}")
        lines.append("Execute all targets (listed as subsequent nodes).")

    loop_ctx = _find_loop_context(nid, wf, state.get("topo_order", []), state, project_path)
    if loop_ctx:
        lines.append(loop_ctx)

    return "\n".join(lines)


def _format_gate_task(
    nid: str, gate_node: GateNode, state: dict, project_path: Path,
) -> str:
    """Format a gate node as a review task."""
    prompt = gate_node.gate_prompt or "Review the output of the preceding step."
    prompt = prompt.replace("{project_path}", str(project_path))

    reads = ", ".join(sorted(gate_node.reads)) if gate_node.reads else "none"

    reloop_targets: list[str] = []
    wf = _get_workflow_cached(state["workflow_name"], project_path)
    for edge in wf.edges:
        if edge.source == nid and edge.condition == VerdictType.RELOOP:
            reloop_targets.append(edge.target)

    lines = [
        f"Gate: {nid}",
        f"Review: {prompt}",
        f"Read: {reads}",
        f"Reloop targets: {reloop_targets if reloop_targets else 'none'}",
        "",
        "Respond with one of:",
        "  PROCEED",
        '  RETRY target=<node_id> feedback="<feedback>"',
        '  HALT reason="<reason>"',
    ]
    return "\n".join(lines)


def _detect_artifact(
    nid: str, node: object, project_path: Path, session_start: float = 0.0,
) -> str | None:
    """Check if a node's output artifact exists. Returns content or None.

    When session_start > 0, files with mtime before that timestamp are
    treated as stale leftovers from a prior run and ignored.
    """
    reviews_dir = project_path / ".factory" / "reviews"

    def _fresh(f: Path) -> bool:
        return session_start <= 0 or f.stat().st_mtime >= session_start

    if isinstance(node, AgentNode):
        role = _role_str(node.role)
        tag = nid.replace(f"{role}_", "").replace(role, "")
        if tag and tag != nid:
            tagged_file = reviews_dir / f"{role}-{tag}-latest.md"
            if tagged_file.exists() and _fresh(tagged_file):
                content = tagged_file.read_text().strip()
                if content:
                    return content
        review_file = reviews_dir / f"{role}-latest.md"
        if review_file.exists() and _fresh(review_file):
            content = review_file.read_text().strip()
            if content:
                return content
        if node.writes:
            for wp in node.writes:
                f = project_path / wp
                if f.exists() and _fresh(f):
                    content = f.read_text().strip()
                    if content:
                        return content
        return None

    elif isinstance(node, Study):
        obs_file = project_path / ".factory" / "strategy" / "observations.md"
        if obs_file.exists() and _fresh(obs_file):
            content = obs_file.read_text().strip()
            if content and len(content) > 50:
                return content
        return None

    elif isinstance(node, FnNode):
        if node.writes:
            all_exist = all(
                (project_path / wp).exists() and _fresh(project_path / wp)
                for wp in node.writes
            )
            if all_exist:
                parts = []
                for wp in node.writes:
                    parts.append((project_path / wp).read_text().strip()[:500])
                return "; ".join(parts) if parts else None
        return None

    elif isinstance(node, ForkNode):
        return f"Fork targets: {', '.join(node.targets)}"

    elif isinstance(node, GateNode):
        return None

    return None


def _auto_evaluate_fn_gate(
    gate_node: GateNode,
    project_path: Path,
    state: dict,
    wf: Workflow,
    order: list[str],
    idx: int,
) -> str | None:
    """Auto-evaluate a fn gate. Returns RETRY/HALT string or None if passed."""
    nid = order[idx]
    assert gate_node.evaluator_command is not None
    cmd = gate_node.evaluator_command.replace("{project_path}", str(project_path))
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=60,
        )
        gate_output = result.stdout.strip()
        gate_passed = result.returncode == 0 and "FAIL" not in gate_output
    except subprocess.TimeoutExpired:
        gate_output = "Gate command timed out"
        gate_passed = False

    state["gate_results"][nid] = "PROCEED" if gate_passed else "HALT"
    state["completed"][nid] = gate_output
    _emit_event(
        project_path, "workflow.tool.gate_eval",
        gate=nid, result="PROCEED" if gate_passed else "HALT",
    )

    if not gate_passed:
        reloop_target = _find_reloop_target(wf, nid)
        if reloop_target:
            iter_key = f"{nid}->{reloop_target}"
            count = state["iteration_counts"].get(iter_key, 0) + 1
            state["iteration_counts"][iter_key] = count

            feedback_log = state.setdefault("feedback_log", {})
            entries = feedback_log.setdefault(reloop_target, [])
            entries.append({
                "gate": nid,
                "iteration": count,
                "feedback": gate_output[:500],
                "timestamp": time.time(),
            })

            if count <= 3:
                if reloop_target in order:
                    state["pointer_idx"] = order.index(reloop_target)
                _save_state(project_path, state)
                return (
                    f"RETRY\nGate {nid} failed: {gate_output}\n"
                    f"Retry from: {reloop_target} (attempt {count}/3)"
                )

        state["status"] = "halted"
        state["pointer_idx"] = idx + 1
        _save_state(project_path, state)
        return f"HALT\nGate {nid} failed: {gate_output}"

    state["pointer_idx"] = idx + 1
    _save_state(project_path, state)
    return None


def tool_peek(project_path: Path, node_id: str) -> str:
    """Return full details for any node without advancing the cursor.

    Shows gate criteria, reads/writes, reloop targets, max iterations —
    everything the CEO needs to understand what a downstream node does.
    """
    state = _load_state(project_path)
    wf = _get_workflow_cached(state["workflow_name"], project_path)

    node = wf.nodes.get(node_id)
    if node is None:
        return f"Unknown node: {node_id}"

    lines = _format_node_task(node_id, node, wf, state, project_path).split("\n")

    if isinstance(node, GateNode):
        reloop_targets: list[str] = []
        halt_targets: list[str] = []
        for edge in wf.edges:
            if edge.source == node_id:
                if edge.condition == VerdictType.RELOOP:
                    reloop_targets.append(edge.target)
                elif edge.condition == VerdictType.HALT:
                    halt_targets.append(edge.target)
        if reloop_targets:
            lines.append(f"Reloop to: {', '.join(reloop_targets)}")
        if halt_targets:
            lines.append(f"Halt skips to: {', '.join(halt_targets)}")

        iter_key_prefix = f"{node_id}->"
        for k, v in state.get("iteration_counts", {}).items():
            if k.startswith(iter_key_prefix):
                lines.append(f"Iterations used: {v}/3")

    if isinstance(node, AgentNode) and node.max_iterations > 1:
        lines.append(f"Max iterations: {node.max_iterations}")

    status = "completed" if node_id in state["completed"] else (
        "current" if state["topo_order"][state["pointer_idx"]] == node_id
        and state["pointer_idx"] < len(state["topo_order"]) else "pending"
    )
    lines.append(f"Status: {status}")

    return "\n".join(lines)


def tool_lookahead(project_path: Path, count: int = 5) -> str:
    """Return the next N nodes with full details, up to and including the next gate.

    Gives the CEO downstream visibility without advancing the cursor.
    """
    state = _load_state(project_path)
    wf = _get_workflow_cached(state["workflow_name"], project_path)
    order = state["topo_order"]
    idx = state["pointer_idx"]

    if idx >= len(order):
        return "DONE — no nodes ahead."

    sections: list[str] = []
    shown = 0
    for i in range(idx + 1, len(order)):
        if shown >= count:
            remaining = len(order) - i
            sections.append(f"... {remaining} more node(s)")
            break

        nid = order[i]
        node = wf.nodes.get(nid)
        if node is None:
            continue

        detail = tool_peek(project_path, nid)
        sections.append(detail)
        shown += 1

    return "\n\n---\n\n".join(sections) if sections else "No nodes ahead."


def _find_loop_context(
    nid: str, wf: Workflow, order: list[str], state: dict, project_path: Path,
) -> str | None:
    """If nid is a RELOOP target, return the full loop context.

    Traces from nid forward through the topo order until it reaches the gate
    that RELOOP's back to nid. Returns a formatted description of every node
    in the loop — gates with their criteria, agents with their roles and
    artifact contracts, etc.
    """
    reloop_gates: list[str] = []
    for edge in wf.edges:
        if edge.condition == VerdictType.RELOOP and edge.target == nid:
            reloop_gates.append(edge.source)

    if not reloop_gates:
        return None

    try:
        nid_idx = order.index(nid)
    except ValueError:
        return None

    gate_indices = []
    for g in reloop_gates:
        try:
            gate_indices.append((order.index(g), g))
        except ValueError:
            pass
    if not gate_indices:
        return None

    farthest_gate_idx, farthest_gate = max(gate_indices, key=lambda x: x[0])

    loop_nodes: list[str] = []
    for i in range(nid_idx + 1, farthest_gate_idx + 1):
        loop_nodes.append(order[i])

    if not loop_nodes:
        return None

    lines = [
        f"LOOP CONTEXT — after {nid}, your output goes through these steps "
        f"(failures RELOOP back to {nid}):",
        "",
    ]

    for loop_nid in loop_nodes:
        node = wf.nodes.get(loop_nid)
        if node is None:
            continue

        if isinstance(node, GateNode):
            gate_type = node.evaluator_type
            lines.append(f"  [{loop_nid}] Gate ({gate_type})")
            if node.gate_prompt:
                prompt = node.gate_prompt.replace("{project_path}", str(project_path))
                lines.append(f"    Criteria: {prompt}")
            if node.evaluator_command:
                cmd = node.evaluator_command.replace("{project_path}", str(project_path))
                lines.append(f"    Command: {cmd}")
            if node.reads:
                lines.append(f"    Reads: {', '.join(sorted(node.reads))}")
            for edge in wf.edges:
                if edge.source == loop_nid and edge.condition == VerdictType.RELOOP:
                    lines.append(f"    On failure: RELOOP to {edge.target}")
                elif edge.source == loop_nid and edge.condition == VerdictType.HALT:
                    lines.append("    On HALT: skip remaining loop steps")

        elif isinstance(node, AgentNode):
            role = _role_str(node.role)
            lines.append(f"  [{loop_nid}] Agent: {role}")
            if node.prompt_template:
                task = node.prompt_template.replace("{project_path}", str(project_path))
                lines.append(f"    Will check: {task[:200]}")
            if node.reads:
                lines.append(f"    Reads: {', '.join(sorted(node.reads))}")
            if node.writes:
                lines.append(f"    Writes: {', '.join(sorted(node.writes))}")

        elif isinstance(node, FnNode):
            lines.append(f"  [{loop_nid}] Function")
            if node.command:
                cmd = node.command.replace("{project_path}", str(project_path))
                lines.append(f"    Command: {cmd}")

        lines.append("")

    return "\n".join(lines)


def _find_reloop_target(wf: Workflow, gate_id: str) -> str | None:
    """Find the RELOOP target for a gate node."""
    for edge in wf.edges:
        if edge.source == gate_id and edge.condition == VerdictType.RELOOP:
            return edge.target
    return None
