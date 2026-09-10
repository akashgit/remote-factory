"""Workflow graph engine — composable primitives for factory orchestration."""

from factory.workflow.executor import ExecutionResult, WorkflowExecutor
from factory.workflow.primitives import (
    AgentConfig,
    AgentNode,
    AgentRole,
    Edge,
    Factory,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    SelectionNode,
    register_agent_role,
    Study,
    SubgraphForkNode,
    Verdict,
    VerdictType,
    Workflow,
)

__all__ = [
    "AgentConfig",
    "AgentNode",
    "AgentRole",
    "Edge",
    "ExecutionResult",
    "Factory",
    "FnNode",
    "ForkNode",
    "GateNode",
    "JoinNode",
    "SelectionNode",
    "Study",
    "register_agent_role",
    "SubgraphForkNode",
    "Verdict",
    "VerdictType",
    "Workflow",
    "WorkflowExecutor",
]
