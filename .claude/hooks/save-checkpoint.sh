#!/bin/bash
# Called by Claude Code Stop and SessionEnd hooks to save checkpoint state.
# Uses cwd (the target project), not CLAUDE_PROJECT_DIR (the factory repo).
TARGET_DIR="$(pwd)"
[ -d "$TARGET_DIR/.factory" ] || exit 0
uv run python -m factory.checkpoint_hook "$TARGET_DIR" 2>/dev/null
exit 0
