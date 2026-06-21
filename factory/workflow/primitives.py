"""Workflow graph primitives — composable types for factory orchestration."""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from factory.models import FactoryConfig, ProjectState


# ── agent pool ───────────────────────────────────────────────────


class AgentRole(str, Enum):
    RESEARCHER = "researcher"
    STRATEGIST = "strategist"
    BUILDER = "builder"
    REVIEWER = "reviewer"
    EVALUATOR = "evaluator"
    FAILURE_ANALYST = "failure_analyst"
    CEO = "ceo"
    ARCHIVIST = "archivist"


class AgentConfig(BaseModel):
    """Configuration for an agent in the pool."""

    model_config = ConfigDict(strict=True, extra="forbid")

    role: AgentRole
    model: str


DEFAULT_AGENT_POOL: dict[str, AgentConfig] = {
    "researcher": AgentConfig(role=AgentRole.RESEARCHER, model="sonnet"),
    "strategist": AgentConfig(role=AgentRole.STRATEGIST, model="opus"),
    "builder": AgentConfig(role=AgentRole.BUILDER, model="opus"),
    "reviewer": AgentConfig(role=AgentRole.REVIEWER, model="opus"),
    "evaluator": AgentConfig(role=AgentRole.EVALUATOR, model="opus"),
    "failure_analyst": AgentConfig(role=AgentRole.FAILURE_ANALYST, model="opus"),
    "ceo": AgentConfig(role=AgentRole.CEO, model="opus"),
    "archivist": AgentConfig(role=AgentRole.ARCHIVIST, model="haiku"),
}


# ── verdicts ─────────────────────────────────────────────────────


class VerdictType(str, Enum):
    PROCEED = "proceed"
    RELOOP = "reloop"
    HALT = "halt"


class Verdict(BaseModel):
    """Algebraic verdict type: Proceed | Reloop(target, feedback, max_iterations) | Halt(reason)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    type: VerdictType
    target: str | None = None
    feedback: str | None = None
    max_iterations: int = 3
    reason: str | None = None

    @model_validator(mode="after")
    def _validate_variant(self) -> Verdict:
        if self.type == VerdictType.RELOOP:
            if not self.target:
                raise ValueError("Reloop verdict requires a target node")
        if self.type == VerdictType.HALT:
            if not self.reason:
                raise ValueError("Halt verdict requires a reason")
        return self

    @staticmethod
    def proceed() -> Verdict:
        return Verdict(type=VerdictType.PROCEED)

    @staticmethod
    def reloop(target: str, feedback: str, max_iterations: int = 3) -> Verdict:
        return Verdict(
            type=VerdictType.RELOOP,
            target=target,
            feedback=feedback,
            max_iterations=max_iterations,
        )

    @staticmethod
    def halt(reason: str) -> Verdict:
        return Verdict(type=VerdictType.HALT, reason=reason)


# ── nodes ────────────────────────────────────────────────────────


class Node(BaseModel):
    """Base node in the workflow graph."""

    model_config = ConfigDict(strict=True, extra="forbid")

    id: str
    reads: set[str] = Field(default_factory=set)
    writes: set[str] = Field(default_factory=set)
    blocking: bool = True


class AgentNode(Node):
    """Node that invokes a Claude Code agent."""

    model_config = ConfigDict(strict=True, extra="forbid")

    role: AgentRole
    model: str = ""
    prompt_template: str = ""
    tools: list[str] = Field(default_factory=list)


class FnNode(Node):
    """Node that runs a deterministic shell command or Python callable."""

    model_config = ConfigDict(strict=True, extra="forbid")

    command: str = ""
    callable_name: str | None = None


class GateNode(Node):
    """Decision node that produces a Verdict."""

    model_config = ConfigDict(strict=True, extra="forbid")

    evaluator_type: Literal["agent", "fn", "user"] = "agent"
    evaluator_role: AgentRole | None = None
    evaluator_command: str | None = None
    gate_prompt: str = ""


class ForkNode(Node):
    """Parallel execution node — launches all targets concurrently."""

    model_config = ConfigDict(strict=True, extra="forbid")

    targets: list[str]


class JoinNode(Node):
    """Barrier node — waits for all sources to complete."""

    model_config = ConfigDict(strict=True, extra="forbid")

    sources: list[str]


class Study(FnNode):
    """Distinguished FnNode wrapping `factory study`."""

    model_config = ConfigDict(strict=True, extra="forbid")

    focus: str | None = None


# ── edges ────────────────────────────────────────────────────────


class Edge(BaseModel):
    """Directed edge in the workflow graph with optional verdict condition."""

    model_config = ConfigDict(strict=True, extra="forbid")

    source: str
    target: str
    condition: VerdictType | None = None


# ── workflow ─────────────────────────────────────────────────────


NodeType = AgentNode | FnNode | GateNode | ForkNode | JoinNode | Study


TriggerFn = Callable[[ProjectState, dict[str, Any]], bool]


class Workflow(BaseModel):
    """A directed graph of typed nodes with labeled edges and a state-based trigger."""

    model_config = ConfigDict(strict=True, extra="forbid", arbitrary_types_allowed=True)

    name: str
    nodes: dict[str, NodeType]
    edges: list[Edge]
    start_node: str
    trigger: TriggerFn | None = Field(default=None, exclude=True)

    def validate_graph(self) -> list[str]:
        """Validate workflow graph structure using NetworkX. Returns list of issues."""
        from factory.workflow.validation import validate_workflow
        return validate_workflow(self)


# ── factory ──────────────────────────────────────────────────────


class Factory(BaseModel):
    """Top-level container: agent pool + workflows + config."""

    model_config = ConfigDict(strict=True, extra="forbid", arbitrary_types_allowed=True)

    agent_pool: dict[str, AgentConfig]
    workflows: dict[str, Workflow]
    config: FactoryConfig | None = None

    def select_workflow(
        self, state: ProjectState, context: dict[str, Any] | None = None,
    ) -> Workflow | None:
        ctx = context or {}
        for wf in self.workflows.values():
            if wf.trigger and wf.trigger(state, ctx):
                return wf
        return None
