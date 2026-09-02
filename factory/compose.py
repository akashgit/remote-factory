"""Mode × Task composition validation layer.

Validates that a workflow (mode) can run a task by checking capability
compatibility, then wires up a ready-to-run InnerLoop.

Imports from factory.task and factory.workflow.primitives directly.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


# ── Capability StrEnum ───────────────────────────────────────────


class Capability(StrEnum):
    """Closed set of capabilities that modes provide and tasks require."""

    CAN_MODIFY_CODE = "can_modify_code"
    CAN_RUN_TESTS = "can_run_tests"
    HAS_BUILDER = "has_builder"
    HAS_RESEARCHER = "has_researcher"
    HAS_STRATEGIST = "has_strategist"
    HAS_QUALITY_GATE = "has_quality_gate"
    HAS_PARALLELISM = "has_parallelism"
    HAS_CODE_REVIEW = "has_code_review"
    HAS_ADVERSARIAL_QA = "has_adversarial_qa"
    HAS_ARCHIVIST = "has_archivist"
    CAN_GENERATE_PROMPTS = "can_generate_prompts"
    CAN_RUN_SUBPROCESS = "can_run_subprocess"
    CAN_ACCESS_NETWORK = "can_access_network"
    HAS_HEALTH_CHECK = "has_health_check"
    CAN_ITERATE = "can_iterate"


# ── IncompatibleCompositionError ─────────────────────────────────


class IncompatibleCompositionError(TypeError):
    """Raised when a mode cannot run a task due to missing capabilities."""

    def __init__(
        self,
        mode_name: str,
        task_name: str,
        mode_missing: set[str],
        task_missing: set[str] | None = None,
        hint: str = "",
    ) -> None:
        self.mode_name = mode_name
        self.task_name = task_name
        self.mode_missing = mode_missing
        self.task_missing = task_missing or set()
        self.hint = hint
        parts = [
            f"Mode '{mode_name}' cannot run task '{task_name}'.",
        ]
        if mode_missing:
            caps = ", ".join(sorted(mode_missing))
            parts.append(f"Mode is missing capabilities: {caps}")
        if self.task_missing:
            caps = ", ".join(sorted(self.task_missing))
            parts.append(f"Task is missing capabilities: {caps}")
        if hint:
            parts.append(f"Hint: {hint}")
        super().__init__(" ".join(parts))


# ── Task Protocol ────────────────────────────────────────────────


@runtime_checkable
class TaskProtocol(Protocol):
    """Runtime-checkable protocol for Task objects (four hooks)."""

    def instances(self) -> Any: ...
    def setup(self, instance: Any, workspace: Path) -> None: ...
    def prompt(self, instance: Any) -> str: ...
    def verify(self, instance: Any, workspace: Path) -> Any: ...


# ── ModeCapabilities ─────────────────────────────────────────────


class ModeCapabilities:
    """Capabilities inferred from a Workflow's node structure."""

    __slots__ = ("provides",)

    def __init__(self, provides: frozenset[Capability]) -> None:
        self.provides = provides

    @classmethod
    def from_workflow(cls, workflow: Any) -> ModeCapabilities:
        """Infer capabilities from the workflow's node structure.

        A workflow with a builder AgentNode → CAN_MODIFY_CODE + HAS_BUILDER.
        A GateNode → HAS_QUALITY_GATE. A ForkNode → HAS_PARALLELISM. Etc.
        """
        from factory.workflow.primitives import (
            AgentNode,
            AgentRole,
            FnNode,
            ForkNode,
            GateNode,
        )

        caps: set[Capability] = set()

        if not hasattr(workflow, "nodes"):
            return cls(frozenset(caps))

        for node in workflow.nodes.values():
            if isinstance(node, AgentNode):
                role = node.role
                if role == AgentRole.BUILDER:
                    caps.add(Capability.CAN_MODIFY_CODE)
                    caps.add(Capability.HAS_BUILDER)
                    caps.add(Capability.CAN_RUN_TESTS)
                    caps.add(Capability.CAN_RUN_SUBPROCESS)
                elif role == AgentRole.RESEARCHER:
                    caps.add(Capability.HAS_RESEARCHER)
                elif role == AgentRole.STRATEGIST:
                    caps.add(Capability.HAS_STRATEGIST)
                elif role == AgentRole.CODE_REVIEWER:
                    caps.add(Capability.HAS_CODE_REVIEW)
                elif role == AgentRole.ADVERSARIAL_TESTER:
                    caps.add(Capability.HAS_ADVERSARIAL_QA)
                elif role == AgentRole.ARCHIVIST:
                    caps.add(Capability.HAS_ARCHIVIST)
                elif role == AgentRole.HEALTH_CHECKER:
                    caps.add(Capability.HAS_HEALTH_CHECK)
                caps.add(Capability.CAN_GENERATE_PROMPTS)

            elif isinstance(node, GateNode):
                caps.add(Capability.HAS_QUALITY_GATE)

            elif isinstance(node, ForkNode):
                caps.add(Capability.HAS_PARALLELISM)

            elif isinstance(node, FnNode):
                caps.add(Capability.CAN_RUN_SUBPROCESS)

        return cls(frozenset(caps))


# ── TaskCapabilities ─────────────────────────────────────────────


class TaskCapabilities:
    """Capabilities a task requires from a mode, inferred from scoring."""

    __slots__ = ("requires",)

    def __init__(self, requires: frozenset[Capability]) -> None:
        self.requires = requires

    @classmethod
    def from_task(cls, task: Any) -> TaskCapabilities:
        """Infer required capabilities from a task's scoring contract."""
        from factory.task import (
            ExactMatchScoring,
            ExitCodeScoring,
            PytestScoring,
        )

        caps: set[Capability] = set()

        scoring = getattr(task, "scoring", None)
        if scoring is None:
            defn = getattr(task, "definition", None)
            if defn is not None:
                scoring = defn.scoring

        if isinstance(scoring, PytestScoring):
            caps.add(Capability.CAN_MODIFY_CODE)
            caps.add(Capability.CAN_RUN_TESTS)
            caps.add(Capability.HAS_BUILDER)
        elif isinstance(scoring, ExitCodeScoring):
            caps.add(Capability.CAN_MODIFY_CODE)
            caps.add(Capability.CAN_RUN_TESTS)
            caps.add(Capability.HAS_BUILDER)
        elif isinstance(scoring, ExactMatchScoring):
            caps.add(Capability.CAN_RUN_SUBPROCESS)

        # Add explicit required_capabilities from constraints
        constraints = getattr(task, "constraints", None)
        if constraints is None:
            defn = getattr(task, "definition", None)
            if defn is not None:
                constraints = defn.constraints
        if constraints is not None:
            for cap_str in getattr(constraints, "required_capabilities", []):
                try:
                    caps.add(Capability(cap_str))
                except ValueError:
                    pass

        return cls(frozenset(caps))


# ── validate_composition ─────────────────────────────────────────


def validate_composition(workflow: Any, task: Any) -> None:
    """Validate that a workflow can run a task.

    Raises IncompatibleCompositionError if capabilities don't match.
    """
    mode_caps = ModeCapabilities.from_workflow(workflow)
    task_caps = TaskCapabilities.from_task(task)

    mode_missing = set(task_caps.requires) - set(mode_caps.provides)

    mode_name = getattr(workflow, "name", "unknown")
    task_name = getattr(task, "name", "unknown")

    if mode_missing:
        raise IncompatibleCompositionError(
            mode_name=mode_name,
            task_name=task_name,
            mode_missing={str(c) for c in mode_missing},
            hint="Try a mode with builder/test capabilities (e.g. 'improve').",
        )


# ── compose() public API ────────────────────────────────────────


def compose(workflow: Any, task: Any, project_dir: str | Path) -> Any:
    """Compose a workflow + task into a ready-to-run InnerLoop.

    1. Protocol check — is this a valid Task?
    2. Composition validation — can this mode run this task?
    3. Derive evaluator from scoring contract
    4. Wire up InnerLoop

    Returns an InnerLoop instance.
    """
    if not isinstance(task, TaskProtocol):
        raise TypeError(
            f"Expected a Task with four hooks (instances, setup, prompt, verify), "
            f"got {type(task).__name__}"
        )

    validate_composition(workflow, task)

    evaluator = task.get_evaluator()

    from factory.inner_loop import InnerLoop

    mode_name = getattr(workflow, "name", "composed")
    return InnerLoop(
        project_dir=Path(project_dir),
        mode=mode_name,
        evaluator=evaluator,
        workflow=workflow,
        task=task,
    )
