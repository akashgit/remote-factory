"""Workflow-specific event types for .factory/events.jsonl."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from factory.workflow.primitives import VerdictType


class WorkflowEvent(BaseModel):
    """Base for workflow events emitted to events.jsonl."""

    model_config = ConfigDict(strict=True, extra="forbid")

    workflow_name: str
    run_id: str


class WorkflowStarted(WorkflowEvent):
    """Emitted when a workflow begins execution."""

    model_config = ConfigDict(strict=True, extra="forbid")

    start_node: str


class NodeStarted(WorkflowEvent):
    """Emitted when a node begins execution."""

    model_config = ConfigDict(strict=True, extra="forbid")

    node_id: str
    node_type: str
    iteration: int = 0


class NodeCompleted(WorkflowEvent):
    """Emitted when a node completes successfully."""

    model_config = ConfigDict(strict=True, extra="forbid")

    node_id: str
    node_type: str
    files_written: list[str] = []
    duration_ms: float = 0.0


class NodeFailed(WorkflowEvent):
    """Emitted when a node fails."""

    model_config = ConfigDict(strict=True, extra="forbid")

    node_id: str
    node_type: str
    error: str


class GateVerdictEvent(WorkflowEvent):
    """Emitted when a gate produces a verdict."""

    model_config = ConfigDict(strict=True, extra="forbid")

    node_id: str
    verdict_type: VerdictType
    target: str | None = None
    feedback: str | None = None
    reason: str | None = None
    iteration: int = 0


class WorkflowCompleted(WorkflowEvent):
    """Emitted when a workflow completes successfully."""

    model_config = ConfigDict(strict=True, extra="forbid")

    nodes_executed: int
    duration_ms: float = 0.0


class WorkflowHalted(WorkflowEvent):
    """Emitted when a workflow is halted by a verdict or error."""

    model_config = ConfigDict(strict=True, extra="forbid")

    reason: str
    halted_at_node: str


def emit_workflow_event(
    project_path: Any,
    event_type: str,
    event: WorkflowEvent,
) -> None:
    """Emit a workflow event to .factory/events.jsonl."""
    from pathlib import Path

    from factory.events import emit_event

    path = Path(project_path) if not isinstance(project_path, Path) else project_path
    emit_event(
        path,
        event_type,
        agent="workflow",
        data=event.model_dump(mode="python"),
    )
