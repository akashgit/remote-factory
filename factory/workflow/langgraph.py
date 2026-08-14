"""Compile Factory workflow definitions to the LangGraph runtime."""

from __future__ import annotations

import operator
import time
from collections.abc import Callable
from typing import Annotated, Any, Protocol, TypedDict, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from factory.workflow.events import (
    GateVerdictEvent,
    NodeCompleted,
    NodeStarted,
    WorkflowCompleted,
    WorkflowEvent,
    WorkflowHalted,
    WorkflowStarted,
)
from factory.workflow.primitives import (
    ForkNode,
    GateNode,
    JoinNode,
    NodeType,
    SelectionNode,
    SubgraphForkNode,
    Verdict,
    VerdictType,
    Workflow,
)


def _merge_unique(left: list[str], right: list[str]) -> list[str]:
    return list(dict.fromkeys([*left, *right]))


def _merge_dict(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {**left, **right}


def _prefer_right(left: str, right: str) -> str:
    return right or left


class FactoryRunState(TypedDict):
    """Small, serializable execution state persisted by LangGraph."""

    run_id: str
    workflow_name: str
    project_path: str
    started_at: float
    completed_nodes: Annotated[list[str], _merge_unique]
    completed_files: Annotated[list[str], _merge_unique]
    node_outputs: Annotated[dict[str, Any], _merge_dict]
    node_context: Annotated[dict[str, Any], _merge_dict]
    node_attempts: Annotated[dict[str, Any], _merge_dict]
    iteration_counts: Annotated[dict[str, Any], _merge_dict]
    feedback_log: Annotated[dict[str, Any], _merge_dict]
    verdicts: Annotated[dict[str, Any], _merge_dict]
    events: Annotated[list[dict[str, Any]], operator.add]
    nodes_executed: Annotated[int, operator.add]
    halted: Annotated[bool, operator.or_]
    halt_reason: Annotated[str, _prefer_right]


class WorkflowRuntime(Protocol):
    """Factory-owned behavior invoked by the LangGraph compiler."""

    workflow: Workflow
    run_id: str
    interactive: bool
    auto_approve: bool

    async def run_node(self, node: NodeType, state: FactoryRunState) -> str: ...

    async def evaluate_gate(self, node: GateNode, state: FactoryRunState) -> Verdict: ...

    def parse_gate_submission(self, node: GateNode, output: str) -> Verdict: ...

    def accept_submission(self, node: NodeType, output: str) -> str: ...

    def emit_event(self, event_type: str, event: WorkflowEvent) -> None: ...


CompiledFactoryGraph = CompiledStateGraph[
    FactoryRunState,
    None,
    FactoryRunState,
    FactoryRunState,
]


def initial_state(workflow: Workflow, project_path: str, run_id: str) -> FactoryRunState:
    """Build the complete input state for a new workflow thread."""
    return {
        "run_id": run_id,
        "workflow_name": workflow.name,
        "project_path": project_path,
        "started_at": 0.0,
        "completed_nodes": [],
        "completed_files": [],
        "node_outputs": {},
        "node_context": {},
        "node_attempts": {},
        "iteration_counts": {},
        "feedback_log": {},
        "verdicts": {},
        "events": [],
        "nodes_executed": 0,
        "halted": False,
        "halt_reason": "",
    }


def compile_langgraph(
    workflow: Workflow,
    runtime: WorkflowRuntime,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledFactoryGraph:
    """Compile the Factory DSL directly to a LangGraph ``StateGraph``."""
    builder = StateGraph(FactoryRunState)
    start_node = "__factory_start__"
    complete_node = "__factory_complete__"
    subgraph_nodes = _nested_subgraph_nodes(workflow)
    compiled_nodes = {
        node_id: node
        for node_id, node in workflow.nodes.items()
        if node_id not in subgraph_nodes
    }

    for node_id, node in compiled_nodes.items():
        if isinstance(node, GateNode):
            destinations = tuple([*_gate_destinations(workflow, node_id), complete_node])
            builder.add_node(
                node_id,
                cast(Any, _gate_callable(workflow, runtime, node)),
                destinations=destinations,
            )
        else:
            builder.add_node(node_id, cast(Any, _action_callable(runtime, node)))

    builder.add_node(start_node, cast(Any, _start_callable(runtime)))
    builder.add_node(complete_node, cast(Any, _complete_callable(runtime)))
    builder.add_edge(START, start_node)
    builder.add_edge(start_node, workflow.start_node)
    wired_sources: set[str] = set()

    for node in compiled_nodes.values():
        if not isinstance(node, JoinNode):
            continue
        sources = [source for source in node.sources if source in compiled_nodes]
        if not sources:
            continue
        builder.add_edge(sources[0] if len(sources) == 1 else sources, node.id)
        wired_sources.update(sources)

    for edge in workflow.edges:
        if edge.source not in compiled_nodes or edge.target not in compiled_nodes:
            continue
        if isinstance(compiled_nodes[edge.source], GateNode):
            continue
        if isinstance(compiled_nodes[edge.target], JoinNode):
            continue
        if edge.condition is None:
            builder.add_edge(edge.source, edge.target)
            wired_sources.add(edge.source)

    for node_id, node in compiled_nodes.items():
        if isinstance(node, GateNode) or node_id in wired_sources:
            continue
        builder.add_edge(node_id, complete_node)

    builder.add_edge(complete_node, END)

    return builder.compile(checkpointer=checkpointer, name=workflow.name)


def _start_callable(runtime: WorkflowRuntime) -> Callable[[FactoryRunState], Any]:
    async def start_workflow(state: FactoryRunState) -> dict[str, Any]:
        started = WorkflowStarted(
            workflow_name=runtime.workflow.name,
            run_id=runtime.run_id,
            start_node=runtime.workflow.start_node,
        )
        event = _record_event(runtime, "workflow.started", started)
        return {"started_at": time.time(), "events": [event]}

    return start_workflow


def _complete_callable(runtime: WorkflowRuntime) -> Callable[[FactoryRunState], Any]:
    async def complete_workflow(state: FactoryRunState) -> dict[str, Any]:
        duration_ms = (time.time() - state["started_at"]) * 1000
        if state["halted"]:
            completed: WorkflowEvent = WorkflowHalted(
                workflow_name=runtime.workflow.name,
                run_id=runtime.run_id,
                reason=state["halt_reason"],
                halted_at_node="unknown",
            )
            event_type = "workflow.halted"
        else:
            completed = WorkflowCompleted(
                workflow_name=runtime.workflow.name,
                run_id=runtime.run_id,
                nodes_executed=state["nodes_executed"],
                duration_ms=duration_ms,
            )
            event_type = "workflow.completed"
        return {"events": [_record_event(runtime, event_type, completed)]}

    return complete_workflow


def _action_callable(
    runtime: WorkflowRuntime,
    node: NodeType,
) -> Callable[[FactoryRunState], Any]:
    async def execute_action(state: FactoryRunState) -> dict[str, Any]:
        submitted: object | None = None
        internal_node = isinstance(
            node,
            (ForkNode, JoinNode, SubgraphForkNode, SelectionNode),
        )
        if runtime.interactive and not internal_node:
            submitted = interrupt({"kind": "node", "node_id": node.id})

        started = NodeStarted(
            workflow_name=runtime.workflow.name,
            run_id=runtime.run_id,
            node_id=node.id,
            node_type=type(node).__name__,
            iteration=int(state["node_attempts"].get(node.id, 0)),
        )
        started_event = _record_event(runtime, "node.started", started)
        start = time.monotonic()
        if submitted is not None:
            output = runtime.accept_submission(node, str(submitted))
        else:
            output = await runtime.run_node(node, state)
        elapsed = (time.monotonic() - start) * 1000
        completed = NodeCompleted(
            workflow_name=runtime.workflow.name,
            run_id=runtime.run_id,
            node_id=node.id,
            node_type=type(node).__name__,
            files_written=sorted(node.writes),
            duration_ms=elapsed,
        )
        completed_event = _record_event(runtime, "node.completed", completed)
        attempt = int(state["node_attempts"].get(node.id, 0)) + 1
        return {
            "completed_nodes": [node.id],
            "completed_files": sorted(node.writes),
            "node_outputs": {node.id: output},
            "node_attempts": {node.id: attempt},
            "events": [started_event, completed_event],
            "nodes_executed": 1,
        }

    return execute_action


def _gate_callable(
    workflow: Workflow,
    runtime: WorkflowRuntime,
    node: GateNode,
) -> Callable[[FactoryRunState], Any]:
    async def execute_gate(state: FactoryRunState) -> Command[Any]:
        needs_input = (
            runtime.interactive and node.evaluator_type != "fn"
        ) or (node.evaluator_type == "user" and not runtime.auto_approve)
        if needs_input:
            submitted = interrupt({"kind": "gate", "node_id": node.id})
            verdict = runtime.parse_gate_submission(node, str(submitted))
        else:
            verdict = await runtime.evaluate_gate(node, state)

        attempt = int(state["node_attempts"].get(node.id, 0)) + 1
        started = NodeStarted(
            workflow_name=workflow.name,
            run_id=runtime.run_id,
            node_id=node.id,
            node_type="GateNode",
            iteration=attempt - 1,
        )
        started_event = _record_event(runtime, "node.started", started)
        gate_event = GateVerdictEvent(
            workflow_name=workflow.name,
            run_id=runtime.run_id,
            node_id=node.id,
            verdict_type=verdict.type,
            target=verdict.target,
            feedback=verdict.feedback,
            reason=verdict.reason,
            iteration=attempt - 1,
        )
        verdict_event = _record_event(runtime, "gate.verdict", gate_event)
        update: dict[str, Any] = {
            "completed_nodes": [node.id],
            "node_attempts": {node.id: attempt},
            "verdicts": {node.id: verdict.model_dump(mode="json")},
            "events": [started_event, verdict_event],
            "nodes_executed": 1,
        }

        target = _gate_target(workflow, node.id, verdict)
        if verdict.type == VerdictType.RELOOP:
            if target is None:
                reason = f"reloop verdict from gate '{node.id}' has no target"
                return Command(
                    update={**update, "halted": True, "halt_reason": reason},
                    goto="__factory_complete__",
                )
            key = f"{node.id}->{target}"
            count = int(state["iteration_counts"].get(key, 0)) + 1
            update["iteration_counts"] = {key: count}
            if count > verdict.max_iterations:
                reason = (
                    f"max iterations ({verdict.max_iterations}) exhausted "
                    f"for gate '{node.id}' -> '{target}'"
                )
                halt_target = _edge_target(workflow, node.id, VerdictType.HALT)
                return Command(
                    update={**update, "halted": True, "halt_reason": reason},
                    goto=halt_target or "__factory_complete__",
                )
            if verdict.feedback:
                existing = str(state["node_context"].get(target, ""))
                feedback = f"[Feedback iteration {count}]: {verdict.feedback}"
                update["node_context"] = {
                    target: f"{existing}\n\n{feedback}" if existing else feedback,
                }
                entries = list(state["feedback_log"].get(target, []))
                update["feedback_log"] = {
                    target: [
                        *entries,
                        {
                            "gate": node.id,
                            "iteration": count,
                            "feedback": verdict.feedback,
                            "timestamp": time.time(),
                        },
                    ],
                }

        if verdict.type == VerdictType.HALT:
            reason = verdict.reason or "gate halted"
            update.update({"halted": True, "halt_reason": reason})

        return Command(update=update, goto=target or "__factory_complete__")

    return execute_gate


def _record_event(
    runtime: WorkflowRuntime,
    event_type: str,
    event: WorkflowEvent,
) -> dict[str, Any]:
    runtime.emit_event(event_type, event)
    return {"type": event_type, **event.model_dump(mode="json")}


def _gate_target(workflow: Workflow, gate_id: str, verdict: Verdict) -> str | None:
    if verdict.type == VerdictType.RELOOP and verdict.target in workflow.nodes:
        return verdict.target
    target = _edge_target(workflow, gate_id, verdict.type)
    if target is not None:
        return target
    if verdict.type == VerdictType.PROCEED:
        return _edge_target(workflow, gate_id, None)
    return None


def _edge_target(
    workflow: Workflow,
    source: str,
    condition: VerdictType | None,
) -> str | None:
    for edge in workflow.edges:
        if edge.source == source and edge.condition == condition:
            return edge.target
    return None


def _gate_destinations(workflow: Workflow, gate_id: str) -> list[str]:
    return list(dict.fromkeys(edge.target for edge in workflow.edges if edge.source == gate_id))


def collect_subgraph_nodes(workflow: Workflow, entry: str, exit_node: str) -> set[str]:
    """Collect nodes reachable from ``entry`` through ``exit_node`` inclusive."""
    edge_index: dict[str, list[str]] = {}
    for edge in workflow.edges:
        edge_index.setdefault(edge.source, []).append(edge.target)

    collected: set[str] = set()
    stack = [entry]
    while stack:
        node_id = stack.pop()
        if node_id in collected:
            continue
        collected.add(node_id)
        if node_id != exit_node:
            stack.extend(edge_index.get(node_id, []))
    return collected


def _nested_subgraph_nodes(workflow: Workflow) -> set[str]:
    nested: set[str] = set()
    for node in workflow.nodes.values():
        if isinstance(node, SubgraphForkNode):
            nested.update(collect_subgraph_nodes(workflow, node.subgraph_entry, node.subgraph_exit))
    return nested
