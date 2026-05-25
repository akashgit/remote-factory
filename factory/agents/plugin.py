"""Plugin agent generation — produce Claude Code subagent files from source prompts."""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml

from factory.agents.runner import _PROMPTS_DIR

log = structlog.get_logger()

_AGENTS_YML = Path(__file__).parent / "agents.yml"
_PLUGIN_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent / "agents"


@dataclass(frozen=True)
class AgentMeta:
    description: str
    model: str
    tools: list[str]


@functools.cache
def load_agent_config() -> dict[str, AgentMeta]:
    """Load agent metadata from agents.yml.

    Only includes roles that also have a prompt file in prompts/.
    """
    raw: dict[str, dict] = yaml.safe_load(_AGENTS_YML.read_text())
    config: dict[str, AgentMeta] = {}
    for role, entry in raw.items():
        if not (_PROMPTS_DIR / f"{role}.md").exists():
            log.debug("plugin.skipped", role=role, reason="no_prompt_file")
            continue
        config[role] = AgentMeta(
            description=entry.get("description", ""),
            model=entry["model"],
            tools=entry.get("tools", []),
        )
    log.info("plugin.config_loaded", roles=list(config.keys()), count=len(config))
    return config


def generate_agent_content(role: str) -> str:
    """Generate a complete plugin agent file for the given role.

    Reads the source prompt from factory/agents/prompts/<role>.md and prepends
    YAML frontmatter and a generated-file header.
    """
    config = load_agent_config()
    if role not in config:
        log.error("plugin.unknown_role", role=role, available=list(config.keys()))
        raise ValueError(f"Unknown agent role: {role!r}")

    meta = config[role]
    prompt = (_PROMPTS_DIR / f"{role}.md").read_text()
    frontmatter = yaml.dump(
        {"name": role, "description": meta.description, "model": meta.model, "tools": meta.tools},
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).rstrip("\n")

    return (
        f"---\n"
        f"{frontmatter}\n"
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

    config = load_agent_config()
    out_of_sync: list[str] = []
    for role in config:
        expected = generate_agent_content(role)
        agent_path = agents_dir / f"{role}.md"

        if not agent_path.exists():
            out_of_sync.append(role)
            continue

        if agent_path.read_text() != expected:
            out_of_sync.append(role)

    return out_of_sync
