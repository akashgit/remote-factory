"""Initialize the user intent ledger for design-v2 workflow.

Called by the init_user_intent FnNode in the design-v2 workflow.
Usage: python3 -c "from factory.workflow.contributed.design_v2.intent_init import main; main()" <project_path>
"""

from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: intent_init.py <project_path>", file=sys.stderr)
        sys.exit(1)

    project = Path(sys.argv[1])
    intent = project / ".factory" / "strategy" / "user-intent.md"

    if intent.exists() and intent.stat().st_size > 0:
        print("User intent ledger already exists, skipping")
        sys.exit(0)

    ts = datetime.datetime.now().isoformat(timespec="seconds")

    backlog = project / ".factory" / "strategy" / "backlog.md"
    idea = os.environ.get("FACTORY_IDEA", "")
    if not idea and backlog.exists() and backlog.stat().st_size > 0:
        lines = backlog.read_text().strip().splitlines()
        idea = lines[0] if lines else ""
    if not idea:
        idea = "No idea provided"

    intent.parent.mkdir(parents=True, exist_ok=True)
    content = f"# User Intent Ledger\n\n## [{ts}] Initial Idea\n{idea}\n"
    intent.write_text(content)
    print(f"User intent ledger initialized at {ts}")


if __name__ == "__main__":
    main()
