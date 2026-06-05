"""Plugin agent generation — produce Claude Code and Codex CLI agent files from source prompts."""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

import yaml

from factory.ace.injector import inject_playbook
from factory.ace.paths import DEFAULTS_DIR as _PLAYBOOKS_DIR
from factory.agents.runner import _PROMPTS_DIR

_AGENTS_YML = Path(__file__).parent / "agents.yml"
_PLUGIN_AGENTS_DIR_CANDIDATE = Path(__file__).resolve().parent.parent.parent / "agents"
_PLUGIN_AGENTS_DIR: Path | None = _PLUGIN_AGENTS_DIR_CANDIDATE if _PLUGIN_AGENTS_DIR_CANDIDATE.is_dir() else None
_CODEX_PLUGIN_AGENTS_DIR_CANDIDATE = Path(__file__).resolve().parent.parent.parent / "codex-agents"
_CODEX_PLUGIN_AGENTS_DIR: Path | None = (
    _CODEX_PLUGIN_AGENTS_DIR_CANDIDATE if _CODEX_PLUGIN_AGENTS_DIR_CANDIDATE.is_dir() else None
)


@dataclass(frozen=True)
class AgentMeta:
    description: str
    model: str  # from agents.yml; not emitted in frontmatter (subagents inherit parent model)
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
            continue
        config[role] = AgentMeta(
            description=entry.get("description", ""),
            model=entry["model"],
            tools=entry.get("tools", []),
        )
    return config


def generate_agent_content(role: str) -> str:
    """Generate a complete plugin agent file for the given role.

    Reads the source prompt from factory/agents/prompts/<role>.md and prepends
    YAML frontmatter and a generated-file header.
    """
    config = load_agent_config()
    if role not in config:
        raise ValueError(f"Unknown agent role: {role!r}")

    meta = config[role]
    prompt = (_PROMPTS_DIR / f"{role}.md").read_text()
    # Only inject factory-default playbooks (not user-local ~/.factory/playbooks/)
    # so that sync_agents.py output is deterministic across machines.
    playbook_path = _PLAYBOOKS_DIR / f"{role}.md"
    if playbook_path.exists():
        playbook = playbook_path.read_text().strip()
        if playbook:
            prompt = inject_playbook(prompt, playbook)
    frontmatter = yaml.dump(
        {"name": role, "description": meta.description, "tools": meta.tools},
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


_READ_ONLY_ROLES = frozenset({"researcher", "reviewer", "evaluator", "strategist", "failure_analyst"})
_WORKSPACE_WRITE_ROLES = frozenset({"builder", "archivist", "distiller", "ceo"})


def _sandbox_mode(role: str) -> str:
    """Map agent role to Codex sandbox mode."""
    if role in _READ_ONLY_ROLES:
        return "read-only"
    if role in _WORKSPACE_WRITE_ROLES:
        return "workspace-write"
    raise ValueError(
        f"Unknown role {role!r}: not in _READ_ONLY_ROLES or _WORKSPACE_WRITE_ROLES"
    )


def _escape_toml_multiline_literal(text: str) -> str:
    """Escape text for a TOML multiline literal string (triple single-quoted).

    TOML literal strings have no escape sequences and no concatenation operator,
    so triple single-quotes cannot appear inside them at all. We lossy-replace
    ''' with '' (virtually never appears in agent prompts).
    """
    return text.replace("'''", "''")


def generate_codex_agent_toml(role: str) -> str:
    """Generate a TOML agent file for Codex CLI.

    Reads the same agents.yml + prompts/*.md sources as generate_agent_content
    but emits TOML with fields: name, description, developer_instructions, sandbox_mode.
    """
    config = load_agent_config()
    if role not in config:
        raise ValueError(f"Unknown agent role: {role!r}")

    meta = config[role]
    prompt = (_PROMPTS_DIR / f"{role}.md").read_text()
    playbook_path = _PLAYBOOKS_DIR / f"{role}.md"
    if playbook_path.exists():
        playbook = playbook_path.read_text().strip()
        if playbook:
            prompt = inject_playbook(prompt, playbook)

    sandbox = _sandbox_mode(role)
    escaped_desc = (
        meta.description.replace("\\", "\\\\").replace('"', '\\"')
        .replace("\n", " ").replace("\t", " ")
    )
    escaped_prompt = _escape_toml_multiline_literal(prompt)

    return (
        f'# GENERATED FILE — do not edit directly.\n'
        f'# Source: factory/agents/prompts/{role}.md\n'
        f'# Run: python scripts/sync_agents.py\n'
        f'\n'
        f'name = "factory-{role}"\n'
        f'description = "{escaped_desc}"\n'
        f'sandbox_mode = "{sandbox}"\n'
        f'\n'
        f"developer_instructions = '''\n"
        f'> **Prerequisite:** The `factory` CLI must be on PATH.\n'
        f'> Install: `uv tool install remote-factory`\n'
        f'\n'
        f"{escaped_prompt}'''\n"
    )


def check_codex_agents_in_sync(agents_dir: Path | None = None) -> list[str]:
    """Compare generated Codex TOML agent files against what's on disk.

    Returns a list of role names that are out of sync (empty = all good).
    """
    if agents_dir is None:
        agents_dir = _CODEX_PLUGIN_AGENTS_DIR
    if agents_dir is None:
        return []

    config = load_agent_config()
    out_of_sync: list[str] = []
    for role in config:
        expected = generate_codex_agent_toml(role)
        agent_path = agents_dir / f"{role}.toml"

        if not agent_path.exists():
            out_of_sync.append(role)
            continue

        if agent_path.read_text() != expected:
            out_of_sync.append(role)

    return out_of_sync


def check_agents_in_sync(agents_dir: Path | None = None) -> list[str]:
    """Compare generated agent files against what's on disk.

    Returns a list of role names that are out of sync (empty = all good).
    """
    if agents_dir is None:
        agents_dir = _PLUGIN_AGENTS_DIR
    if agents_dir is None:
        return []

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
