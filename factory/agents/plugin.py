"""Plugin agent generation — produce Claude Code subagent files from source prompts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from factory.agents.runner import AgentRole

log = structlog.get_logger()

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_PLUGIN_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent / "agents"


@dataclass(frozen=True)
class AgentMeta:
    description: str
    model: str
    tools: list[str]


AGENT_CONFIG: dict[AgentRole, AgentMeta] = {
    "researcher": AgentMeta(
        description=(
            "Deep research and discovery for software projects. "
            "Analyzes codebases, researches best practices, and synthesizes findings "
            "to inform strategy. Use when the user wants to study a project or research "
            "a domain before making changes."
        ),
        model="sonnet",
        tools=["Bash", "Read", "WebSearch", "WebFetch", "Grep", "Glob"],
    ),
    "strategist": AgentMeta(
        description=(
            "Generate prioritized improvement hypotheses for a software project. "
            "Reads backlog, experiment history, and eval scores to produce a ranked "
            "strategy. Use when the user wants a plan for what to improve next."
        ),
        model="sonnet",
        tools=["Bash", "Read", "Grep", "Glob"],
    ),
    "builder": AgentMeta(
        description=(
            "Implement a single focused change and open a pull request. "
            "Reads issues, writes code, runs tests, and creates PRs on feature branches. "
            "Use when the user wants a specific feature built or bug fixed."
        ),
        model="sonnet",
        tools=["Bash", "Read", "Edit", "Write", "Grep", "Glob"],
    ),
    "reviewer": AgentMeta(
        description=(
            "Review pull requests against guard rules, eval scores, and code quality criteria. "
            "Makes keep/revert decisions on changes. Use when the user wants a structured "
            "code review or keep/revert verdict."
        ),
        model="sonnet",
        tools=["Bash", "Read", "Grep", "Glob"],
    ),
    "evaluator": AgentMeta(
        description=(
            "Run project evaluations and interpret the results. "
            "Executes eval commands, compares before/after scores, and explains trends. "
            "Use when the user wants to measure project quality or understand score changes."
        ),
        model="sonnet",
        tools=["Bash", "Read", "Grep", "Glob"],
    ),
    "archivist": AgentMeta(
        description=(
            "Record experiment results, research findings, and project knowledge to an "
            "Obsidian vault or local archive. Maintains institutional memory across cycles. "
            "Use when the user wants to archive findings or update project dashboards."
        ),
        model="sonnet",
        tools=["Bash", "Read", "Write", "Grep", "Glob"],
    ),
    "distiller": AgentMeta(
        description=(
            "Transform vague ideas into precise, buildable project specifications. "
            "Combines user intent with research findings to produce structured specs. "
            "Use when the user has a raw idea that needs refining into a concrete plan."
        ),
        model="sonnet",
        tools=["Read", "Write"],
    ),
    "ceo": AgentMeta(
        description=(
            "Autonomous orchestrator for the full Factory workflow. "
            "Detects project state, spawns specialist agents, runs experiments, "
            "makes keep/revert decisions, and ensures mandatory archival. "
            "Use when the user wants a complete evolution cycle."
        ),
        model="opus",
        tools=["Bash", "Read", "Write", "Edit", "Grep", "Glob", "WebSearch", "WebFetch"],
    ),
}


def generate_agent_content(role: AgentRole) -> str:
    """Generate a complete plugin agent file for the given role.

    Reads the source prompt from factory/agents/prompts/<role>.md and prepends
    YAML frontmatter and a generated-file header.
    """
    if role not in AGENT_CONFIG:
        log.error("plugin_generate_unknown_role", role=role)
        raise ValueError(f"Unknown agent role: {role!r}")

    meta = AGENT_CONFIG[role]
    prompt_path = _PROMPTS_DIR / f"{role}.md"
    if not prompt_path.exists():
        log.error("plugin_generate_prompt_missing", role=role, path=str(prompt_path))
        raise FileNotFoundError(f"Source prompt not found: {prompt_path}")

    prompt = prompt_path.read_text()
    tools_yaml = "\n".join(f"  - {t}" for t in meta.tools)

    log.info("plugin_agent_generated", role=role)
    return (
        f"---\n"
        f"name: {role}\n"
        f"description: \"{meta.description}\"\n"
        f"model: {meta.model}\n"
        f"tools:\n"
        f"{tools_yaml}\n"
        f"---\n"
        f"\n"
        f"<!-- GENERATED FILE — do not edit directly.\n"
        f"     Source: factory/agents/prompts/{role}.md\n"
        f"     Run: python scripts/sync_agents.py -->\n"
        f"\n"
        f"> **Prerequisite:** The `factory` CLI must be on PATH.\n"
        f"> Install: `uv tool install remote-factory`\n"
        f"\n"
        f"{prompt}"
    )


def check_agents_in_sync(agents_dir: Path | None = None) -> list[str]:
    """Compare generated agent files against what's on disk.

    Returns a list of role names that are out of sync (empty = all good).
    """
    if agents_dir is None:
        agents_dir = _PLUGIN_AGENTS_DIR

    out_of_sync: list[str] = []
    for role in AGENT_CONFIG:
        expected = generate_agent_content(role)
        agent_path = agents_dir / f"{role}.md"

        if not agent_path.exists():
            out_of_sync.append(role)
            continue

        if agent_path.read_text() != expected:
            out_of_sync.append(role)

    log.info("plugin_sync_check", out_of_sync_count=len(out_of_sync), out_of_sync=out_of_sync)
    return out_of_sync
