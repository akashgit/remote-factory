#!/bin/bash
# PostToolUse variant: only checkpoints after factory CLI commands.
TARGET_DIR="$(pwd)"
[ -d "$TARGET_DIR/.factory" ] || exit 0
INPUT=$(cat)
CMD=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null || echo "")
if echo "$CMD" | grep -qE "factory (agent|eval|begin|finalize|guard|precheck)"; then
    uv run python -m factory.checkpoint_hook "$TARGET_DIR" 2>/dev/null
fi
exit 0
