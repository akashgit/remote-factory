"""DAG context derivation for the skill review agent.

Extracts contextual information from a workflow DAG to help the
review agent make informed improvements to skill template slots:
- Agent prompts for each role referenced in the DAG
- CLI help for commands used in FnNode steps
- Edge topology as structured context
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factory.workflow.primitives import (
    AgentNode,
    FnNode,
    GateNode,
    Workflow,
)

PROMPTS_DIR = Path(__file__).parent.parent / "agents" / "prompts"


def derive_context(workflow: Workflow) -> dict[str, Any]:
    """Derive a context bundle from a workflow DAG for the review agent.

    Returns a dict with:
    - agent_prompts: {role_name: prompt_text} for each role in the DAG
    - commands: {node_id: command_string} for each FnNode
    - edge_topology: structured edge list
    - node_summary: brief summary of each node
    """
    return {
        "agent_prompts": _extract_agent_prompts(workflow),
        "commands": _extract_commands(workflow),
        "edge_topology": _extract_edge_topology(workflow),
        "node_summary": _extract_node_summary(workflow),
    }


def _extract_agent_prompts(workflow: Workflow) -> dict[str, str]:
    """Read agent prompt files for each role referenced in the DAG."""
    roles: set[str] = set()

    for node in workflow.nodes.values():
        if isinstance(node, AgentNode):
            roles.add(node.role.value)
        elif isinstance(node, GateNode) and node.evaluator_role:
            roles.add(node.evaluator_role.value)

    prompts: dict[str, str] = {}
    for role in sorted(roles):
        prompt_path = PROMPTS_DIR / f"{role}.md"
        if prompt_path.exists():
            prompts[role] = prompt_path.read_text()

    return prompts


def _extract_commands(workflow: Workflow) -> dict[str, str]:
    """Extract CLI commands from FnNode and GateNode evaluator_commands."""
    commands: dict[str, str] = {}
    for node_id, node in workflow.nodes.items():
        if isinstance(node, FnNode) and node.command:
            commands[node_id] = node.command
        elif isinstance(node, GateNode) and node.evaluator_command:
            commands[node_id] = node.evaluator_command
    return commands


def _extract_edge_topology(workflow: Workflow) -> list[dict[str, str | None]]:
    """Extract edge topology as a structured list."""
    result: list[dict[str, str | None]] = []
    for edge in workflow.edges:
        result.append({
            "source": edge.source,
            "target": edge.target,
            "condition": edge.condition.value if edge.condition else None,
        })
    return result


def _extract_node_summary(workflow: Workflow) -> dict[str, dict[str, Any]]:
    """Extract a brief summary of each node for context."""
    summary: dict[str, dict[str, Any]] = {}
    for node_id, node in workflow.nodes.items():
        info: dict[str, Any] = {"type": type(node).__name__}
        if isinstance(node, AgentNode):
            info["role"] = node.role.value
            info["blocking"] = node.blocking
            if node.timeout:
                info["timeout"] = node.timeout
        elif isinstance(node, GateNode):
            info["evaluator_type"] = node.evaluator_type
            if node.evaluator_role:
                info["evaluator_role"] = node.evaluator_role.value
        elif isinstance(node, FnNode):
            info["command"] = node.command[:80]
        if node.reads:
            info["reads"] = sorted(node.reads)
        if node.writes:
            info["writes"] = sorted(node.writes)
        summary[node_id] = info
    return summary


def format_context_for_agent(context: dict[str, Any]) -> str:
    """Format the derived context as a text block for the review agent prompt."""
    parts: list[str] = []

    parts.append("## Agent Prompts\n")
    for role, prompt in context.get("agent_prompts", {}).items():
        parts.append(f"### {role}\n")
        parts.append(prompt[:2000])
        parts.append("")

    parts.append("## CLI Commands Referenced\n")
    for node_id, cmd in context.get("commands", {}).items():
        parts.append(f"- `{node_id}`: `{cmd}`")
    parts.append("")

    parts.append("## Edge Topology\n")
    for edge in context.get("edge_topology", []):
        cond = edge.get("condition") or "unconditional"
        parts.append(f"- {edge['source']} → {edge['target']} ({cond})")
    parts.append("")

    parts.append("## Node Summary\n")
    for node_id, info in context.get("node_summary", {}).items():
        parts.append(f"- `{node_id}`: {info['type']}")

    return "\n".join(parts)
