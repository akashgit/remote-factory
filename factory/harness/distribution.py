"""Distribution bundle descriptors and agent package emitters."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from factory.harness.models import DistributionBundle


class DistributionTarget(str, Enum):
    """Supported generated agent targets."""

    CLAUDE = "claude"
    CODEX = "codex"


class AgentRoleSpec(BaseModel):
    """Native description of an agent role before target-specific emission."""

    model_config = ConfigDict(strict=True, extra="forbid")

    role: str
    description: str
    model: str
    tools: list[str] = Field(default_factory=list)
    prompt_source: str


class AgentPackageSpec(BaseModel):
    """Native package spec for generated agent distributions."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str = "factory"
    roles: dict[str, AgentRoleSpec] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)


def cli_local_bundle() -> DistributionBundle:
    """Describe the current primary distribution."""
    return DistributionBundle(
        name="cli-local",
        description="Factory CLI with local subprocess agents and .factory state.",
        surface="factory CLI",
        runtime="local_agent_runtime",
        state_backend="local_factory_state",
        guardrails=[
            "eval.runner",
            "precheck",
            "hard_constraints",
            "leakage",
            "clean_pr",
        ],
        emitters=["claude_agent_files", "codex_agent_files"],
        metadata={"primary": "true"},
    )


def build_current_agent_package_spec() -> AgentPackageSpec:
    """Build an agent package spec from the current agents.yml + prompts sources."""
    from factory.agents.plugin import load_agent_config
    from factory.agents.runner import _PROMPTS_DIR

    roles: dict[str, AgentRoleSpec] = {}
    for role, meta in load_agent_config().items():
        roles[role] = AgentRoleSpec(
            role=role,
            description=meta.description,
            model=meta.model,
            tools=list(meta.tools),
            prompt_source=str(_PROMPTS_DIR / f"{role}.md"),
        )
    return AgentPackageSpec(
        roles=roles,
        metadata={"source": "factory.agents.plugin.load_agent_config"},
    )


def emit_current_agent_role(role: str, target: DistributionTarget) -> str:
    """Emit generated agent content using current compatibility functions."""
    from factory.agents.plugin import generate_agent_content, generate_codex_agent_toml

    if target == DistributionTarget.CODEX:
        return generate_codex_agent_toml(role)
    return generate_agent_content(role)
