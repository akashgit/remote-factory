#!/usr/bin/env python3
"""Sync plugin agent files from source prompts.

Usage:
    python scripts/sync_agents.py          # Generate/update agents/*.md
    python scripts/sync_agents.py --check  # Verify sync (exits non-zero if stale)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the factory package is importable when running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory.agents.plugin import (
    check_agents_in_sync,
    check_codex_agents_in_sync,
    generate_agent_content,
    generate_codex_agent_toml,
    load_agent_config,
)

_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
_CODEX_AGENTS_DIR = Path(__file__).resolve().parent.parent / "codex-agents"


def main() -> int:
    check_mode = "--check" in sys.argv

    if check_mode:
        out_of_sync = check_agents_in_sync(_AGENTS_DIR)
        codex_out_of_sync = check_codex_agents_in_sync(_CODEX_AGENTS_DIR)
        all_issues = out_of_sync + [f"{r} (codex)" for r in codex_out_of_sync]
        if all_issues:
            print(f"Out of sync: {', '.join(all_issues)}", file=sys.stderr)
            print("Run: python scripts/sync_agents.py", file=sys.stderr)
            return 1
        print("All plugin agents are in sync.")
        return 0

    _AGENTS_DIR.mkdir(exist_ok=True)
    _CODEX_AGENTS_DIR.mkdir(exist_ok=True)

    config = load_agent_config()
    for role in config:
        content = generate_agent_content(role)
        out_path = _AGENTS_DIR / f"{role}.md"
        out_path.write_text(content)
        print(f"  {role} -> {out_path}")

    for role in config:
        toml_content = generate_codex_agent_toml(role)
        toml_path = _CODEX_AGENTS_DIR / f"{role}.toml"
        toml_path.write_text(toml_content)
        print(f"  {role} -> {toml_path}")

    print(f"\nGenerated {len(config)} agent files in {_AGENTS_DIR} (Markdown)")
    print(f"Generated {len(config)} agent files in {_CODEX_AGENTS_DIR} (TOML)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
