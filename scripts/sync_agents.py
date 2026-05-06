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

from factory.agents.plugin import check_agents_in_sync, generate_agent_content, load_agent_config

_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"


def main() -> int:
    check_mode = "--check" in sys.argv

    if check_mode:
        out_of_sync = check_agents_in_sync(_AGENTS_DIR)
        if out_of_sync:
            print(f"Out of sync: {', '.join(out_of_sync)}", file=sys.stderr)
            print("Run: python scripts/sync_agents.py", file=sys.stderr)
            return 1
        print("All plugin agents are in sync.")
        return 0

    _AGENTS_DIR.mkdir(exist_ok=True)

    for role in load_agent_config():
        content = generate_agent_content(role)
        out_path = _AGENTS_DIR / f"{role}.md"
        out_path.write_text(content)
        print(f"  {role} -> {out_path}")

    print(f"\nGenerated {len(load_agent_config())} agent files in {_AGENTS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
