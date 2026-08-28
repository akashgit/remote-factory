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
    HEALTH_CHECKER = "health_checker"
    CODE_REVIEWER = "code_reviewer"
    ADVERSARIAL_TESTER = "adversarial_tester"
    FAILURE_ANALYST = "failure_analyst"
    CEO = "ceo"
    ARCHIVIST = "archivist"
    REFINER = "refiner"


class AgentConfig(BaseModel):
    """Configuration for an agent in the pool."""

    model_config = ConfigDict(strict=True, extra="forbid")

    role: AgentRole
    model: str
    timeout: int = 600


DEFAULT_AGENT_POOL: dict[str, AgentConfig] = {
    "researcher": AgentConfig(role=AgentRole.RESEARCHER, model="sonnet", timeout=600),
    "strategist": AgentConfig(role=AgentRole.STRATEGIST, model="opus", timeout=600),
    "builder": AgentConfig(role=AgentRole.BUILDER, model="opus", timeout=1200),
    "health_checker": AgentConfig(role=AgentRole.HEALTH_CHECKER, model="opus", timeout=600),
    "code_reviewer": AgentConfig(role=AgentRole.CODE_REVIEWER, model="opus", timeout=900),
    "adversarial_tester": AgentConfig(
        role=AgentRole.ADVERSARIAL_TESTER, model="opus", timeout=1800
    ),
    "failure_analyst": AgentConfig(role=AgentRole.FAILURE_ANALYST, model="opus", timeout=600),
    "ceo": AgentConfig(role=AgentRole.CEO, model="opus", timeout=3600),
    "archivist": AgentConfig(role=AgentRole.ARCHIVIST, model="haiku", timeout=300),
    "refiner": AgentConfig(role=AgentRole.REFINER, model="opus", timeout=600),
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


class ArtifactCheck(BaseModel):
    """Validation rule for an agent-produced artifact."""

    model_config = ConfigDict(strict=True, extra="forbid")

    path: str
    must_exist: bool = True
    min_size: int = 0
    must_contain: list[str] = Field(default_factory=list)


class AgentNode(Node):
    """Node that invokes a Claude Code agent."""

    model_config = ConfigDict(strict=True, extra="forbid")

    role: AgentRole
    model: str = ""
    prompt_template: str = ""
    tools: list[str] = Field(default_factory=list)
    timeout: int | None = None
    max_iterations: int = 1
    post_checks: list[ArtifactCheck] = Field(default_factory=list)


class FnNode(Node):
    """Node that runs a deterministic shell command or Python callable."""

    model_config = ConfigDict(strict=True, extra="forbid")

    command: str = ""
    callable_name: str | None = None
    notes: str = ""


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


class SubgraphForkNode(Node):
    """Fan-out to N copies of a subgraph, each in an isolated worktree.

    The executor creates independent WorkflowExecutor instances per branch,
    each with its own worktree branching from the same base commit.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    subgraph_entry: str
    subgraph_exit: str
    parallelism: int = 3
    worktree_isolated: bool = True


class SelectionNode(Node):
    """Compare N completed experiment branches and select the best."""

    model_config = ConfigDict(strict=True, extra="forbid")

    strategy: Literal["best_score"] = "best_score"


class Study(FnNode):
    """Distinguished FnNode wrapping `factory study`."""

    model_config = ConfigDict(strict=True, extra="forbid")

    focus: str | None = None


class ToolDef(BaseModel):
    """A tool available to the LLM during a tool-use loop."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    executor: Literal["bash", "file_read", "file_write", "file_edit"] = "bash"


class LLMNode(Node):
    """Node that makes direct LLM API calls with a configurable tool-use loop.

    Unlike AgentNode (full CLI subprocess), this runs the API loop in-process
    with a minimal, configurable tool set.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    system_prompt: str = ""
    instance_prompt: str = ""
    model: str = "sonnet"
    provider: Literal["anthropic", "vertex", "litellm"] = "anthropic"
    max_tokens: int = 8192
    max_turns: int = 50
    temperature: float = 0.0
    stop_sequences: list[str] = Field(default_factory=list)
    tools: list[ToolDef] = Field(default_factory=list)
    tool_choice: Literal["auto", "any", "none"] = "auto"
    timeout: int = 600


# ── edges ────────────────────────────────────────────────────────


class Edge(BaseModel):
    """Directed edge in the workflow graph with optional verdict condition."""

    model_config = ConfigDict(strict=True, extra="forbid")

    source: str
    target: str
    condition: VerdictType | None = None


# ── workflow ─────────────────────────────────────────────────────


NodeType = (
    AgentNode | FnNode | GateNode | ForkNode | JoinNode | SubgraphForkNode | SelectionNode | Study | LLMNode
)


TriggerFn = Callable[[ProjectState, dict[str, Any]], bool]


class Workflow(BaseModel):
    """A directed graph of typed nodes with labeled edges and a state-based trigger."""

    model_config = ConfigDict(strict=True, extra="forbid", arbitrary_types_allowed=True)

    name: str
    nodes: dict[str, NodeType]
    edges: list[Edge]
    start_node: str
    terminal: bool = False
    trigger: TriggerFn | None = Field(default=None, exclude=True)
    knob_values: dict[str, str | float] = Field(default_factory=dict)
    knob_bounds: dict[str, list[str | float]] = Field(default_factory=dict)
    knob_expandable: dict[str, str] = Field(default_factory=dict)

    def validate_graph(self) -> list[str]:
        """Validate workflow graph structure using NetworkX. Returns list of issues."""
        from factory.workflow.validation import validate_workflow

        return validate_workflow(self)

    def subgraph(
        self,
        node_ids: set[str],
        *,
        name: str,
        start_node: str,
    ) -> Workflow:
        """Extract a subgraph containing only the specified nodes.

        Deep-copies requested nodes and filters edges to only those
        where both source and target are in node_ids.
        """
        nodes: dict[str, NodeType] = {}
        for nid in node_ids:
            if nid not in self.nodes:
                raise ValueError(f"node '{nid}' not found in workflow '{self.name}'")
            nodes[nid] = self.nodes[nid].model_copy(deep=True)
        edges = [
            e.model_copy(deep=True)
            for e in self.edges
            if e.source in node_ids and e.target in node_ids
        ]
        return Workflow(name=name, nodes=nodes, edges=edges, start_node=start_node)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the workflow to a JSON-safe dict."""
        nodes_out: dict[str, Any] = {}
        for nid, node in self.nodes.items():
            d = node.model_dump(mode="json")
            d["_type"] = type(node).__name__
            nodes_out[nid] = d

        edges_out = [e.model_dump(mode="json") for e in self.edges]

        return {
            "name": self.name,
            "nodes": nodes_out,
            "edges": edges_out,
            "start_node": self.start_node,
            "terminal": self.terminal,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Workflow:
        """Reconstruct a Workflow from a dict produced by ``to_dict``."""
        _NODE_TYPE_MAP: dict[str, type[Node]] = {
            "AgentNode": AgentNode,
            "FnNode": FnNode,
            "GateNode": GateNode,
            "ForkNode": ForkNode,
            "JoinNode": JoinNode,
            "SubgraphForkNode": SubgraphForkNode,
            "SelectionNode": SelectionNode,
            "Study": Study,
            "LLMNode": LLMNode,
        }
        _SET_FIELDS = {"reads", "writes"}

        nodes: dict[str, NodeType] = {}
        for nid, node_data in data["nodes"].items():
            node_data = dict(node_data)
            type_name = node_data.pop("_type", "FnNode")
            node_cls = _NODE_TYPE_MAP.get(type_name)
            if node_cls is None:
                raise ValueError(f"Unknown node type: {type_name}")
            for fld in _SET_FIELDS:
                if fld in node_data and isinstance(node_data[fld], list):
                    node_data[fld] = set(node_data[fld])
            nodes[nid] = node_cls.model_validate(node_data, strict=False)  # type: ignore[assignment]

        edges = [Edge.model_validate(e, strict=False) for e in data["edges"]]

        return cls(
            name=data["name"],
            nodes=nodes,
            edges=edges,
            start_node=data["start_node"],
            terminal=data.get("terminal", False),
        )


# ── factory ──────────────────────────────────────────────────────


class Factory(BaseModel):
    """Top-level container: agent pool + workflows + config."""

    model_config = ConfigDict(strict=True, extra="forbid", arbitrary_types_allowed=True)

    agent_pool: dict[str, AgentConfig]
    workflows: dict[str, Workflow]
    config: FactoryConfig | None = None
