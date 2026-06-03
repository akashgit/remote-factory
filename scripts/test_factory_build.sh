#!/usr/bin/env bash
# =============================================================================
# Factory Agent Runner E2E Test
#
# Creates a fresh GitHub repo, writes a Snake game spec, runs the full
# factory CEO loop, and validates the output.
#
# Usage:
#   ./scripts/test_factory_build.sh                    # default: claude runner
#   ./scripts/test_factory_build.sh --runner codex      # codex runner
#   CODEX_MODEL=gpt-5.4-mini ./scripts/test_factory_build.sh --runner codex
# =============================================================================
set -euo pipefail

# -- Parse args ---------------------------------------------------------------
RUNNER="claude"
if [ "${1:-}" = "--runner" ] && [ -n "${2:-}" ]; then
    RUNNER="$2"
fi

# -- Resolve local factory (never use global install) -------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FACTORY="uv run --project $SCRIPT_DIR factory"

if ! command -v "$RUNNER" &>/dev/null; then
    echo "ERROR: '$RUNNER' CLI not found." >&2; exit 1
fi

# -- Create fresh GitHub repo with random suffix ------------------------------
SUFFIX=$(openssl rand -hex 3)
REPO_NAME="factory-test-${SUFFIX}"
WORKDIR=$(mktemp -d)/"$REPO_NAME"

echo "=============================================="
echo "  Factory E2E Build Test"
echo "  Runner:  $RUNNER"
echo "  Repo:    $REPO_NAME"
echo "  Workdir: $WORKDIR"
echo "=============================================="
echo ""

# Cleanup: delete the GitHub repo on exit
cleanup() {
    echo ""
    echo "[cleanup] Deleting GitHub repo $REPO_NAME..."
    gh repo delete "gx-ai-architect/$REPO_NAME" --yes 2>/dev/null || true
    echo "[cleanup] Project preserved locally at: $WORKDIR"
}
trap cleanup EXIT

echo "[setup] Creating GitHub repo..."
gh repo create "$REPO_NAME" --public --description "Factory e2e test (auto-delete)" --clone 2>/dev/null
cd "$REPO_NAME" 2>/dev/null || { mkdir -p "$WORKDIR" && cd "$WORKDIR" && git init -q; }

# Write spec
cat > factory.md <<'SPEC'
# Snake Game

A browser-based Snake game as a single-page web app.

## Requirements
- Pure HTML/CSS/JavaScript, no frameworks, everything in one `index.html`
- Canvas-based rendering, 400x400 pixel game board, 20x20 grid
- Snake starts at center, moving right, length 3
- Arrow keys to change direction (prevent reversing)
- Food spawns at random grid positions
- Eating food grows snake by 1, score += 10
- Game over on wall or self collision
- Display score and high score (localStorage)
- Game over screen with "Press Space to restart"
- Speed increases every 5 food eaten
- Dark background, green snake, red food, white text

## Tech Stack
- HTML5 Canvas
- Vanilla JavaScript (no build tools, no npm)

## Eval
Run `node -e "require('fs').readFileSync('index.html','utf8')"` to verify the file exists and is readable.
SPEC

git add factory.md
git commit -q -m "add spec"
git push -q origin main 2>/dev/null || true

# -- Build model flag ---------------------------------------------------------
MODEL_FLAG=""
if [ "$RUNNER" = "codex" ] && [ -n "${CODEX_MODEL:-}" ]; then
    MODEL_FLAG="--model $CODEX_MODEL"
fi

# -- Run factory CEO with clean logging ---------------------------------------
echo ""
echo "[test] Starting factory CEO (runner=$RUNNER)"
echo "[test] $(date '+%H:%M:%S')"
echo ""

START_TIME=$(date +%s)
PROJECT_DIR="$(pwd)"

# Background watcher: tail events.jsonl files for sub-agent activity
# The CEO spawns agents via shell commands inside its process — their events
# go to events.jsonl in worktrees, not to stdout. This watcher surfaces them.
(
    sleep 5  # wait for worktree creation
    while true; do
        EVENTS_FILES=$(find "$PROJECT_DIR" -name "events.jsonl" 2>/dev/null)
        for f in $EVENTS_FILES; do
            # Use a marker file to track what we've already printed
            MARKER="${f}.printed"
            TOTAL=$(wc -l < "$f" 2>/dev/null | tr -d ' ')
            SEEN=$(cat "$MARKER" 2>/dev/null || echo 0)
            if [ "$TOTAL" -gt "$SEEN" ]; then
                tail -n +$((SEEN + 1)) "$f" | while IFS= read -r line; do
                    python3 -c "
import json, sys
e = json.loads(sys.argv[1])
t = e.get('type','')
a = e.get('agent','?')
r = e.get('data',{}).get('runner','?')
if 'started' in t and 'agent' in t:
    print(f'  ▶ {a} started (runner={r})')
elif 'completed' in t and 'agent' in t:
    print(f'  ✅ {a} completed (runner={r})')
elif 'failed' in t and 'agent' in t:
    rc = e.get('data',{}).get('return_code','?')
    print(f'  ❌ {a} FAILED (runner={r}, exit={rc})')
elif 'cycle' in t:
    print(f'  ⚙ {t}')
elif 'sprint' in t:
    print(f'  ⚙ {t}')
" "$line" 2>/dev/null
                done
                echo "$TOTAL" > "$MARKER"
            fi
        done
        sleep 3
    done
) &
WATCHER_PID=$!

# Run factory CEO — filter stdout for state transitions only
$FACTORY ceo "$PROJECT_DIR" \
    --runner "$RUNNER" \
    --headless \
    $MODEL_FLAG 2>&1 | while IFS= read -r line; do

    # State machine transitions
    if echo "$line" | grep -q "State:.*→"; then
        echo "  ⚙ $line"
    elif echo "$line" | grep -q "Chaining:"; then
        echo "  ⚙ $line"
    elif echo "$line" | grep -q "mode:"; then
        echo "  ⚙ $line"

    # CEO-level agent events (from invoke_agent stderr)
    elif echo "$line" | grep -q "\[factory\].*FAILED"; then
        echo "  ❌ $line"

    # Cycle events
    elif echo "$line" | grep -q "cycle_state_created\|cycle_state_deleted\|ceo_spawn\|ceo_aborted"; then
        echo "  ⚙ $line"

    # Worktree operations
    elif echo "$line" | grep -q "worktree_create\b"; then
        echo "  🌿 worktree created"
    elif echo "$line" | grep -q "worktree_remove\b"; then
        echo "  🌿 worktree removed"
    fi
done

CEO_EXIT=${PIPESTATUS[0]}

# Kill the background watcher
kill $WATCHER_PID 2>/dev/null
wait $WATCHER_PID 2>/dev/null
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

# -- Results ------------------------------------------------------------------
echo ""
echo "=============================================="
echo "  RESULTS  (${DURATION}s elapsed)"
echo "=============================================="
echo ""
echo "Exit: $CEO_EXIT"

# Agent timeline from events
echo ""
echo "--- Timeline ---"
find . -name "events.jsonl" -not -path "./.factory/worktrees/*" 2>/dev/null | head -1 | while read f; do
    python3 -c "
import json
for line in open('$f'):
    e = json.loads(line)
    ts = e.get('timestamp','')[11:19]
    t = e.get('type','')
    a = e.get('agent','?')
    r = e.get('data',{}).get('runner','?')
    if 'started' in t or 'completed' in t or 'failed' in t:
        icon = '✅' if 'completed' in t else ('❌' if 'failed' in t else '▶')
        print(f'  {ts}  {icon} {a:12s}  runner={r}')
" 2>/dev/null
done

# Runner check
echo ""
EXPECTED="$RUNNER"
EVENTS_FILE=$(find . -name "events.jsonl" -not -path "./.factory/worktrees/*" 2>/dev/null | head -1)
if [ -n "$EVENTS_FILE" ]; then
    MATCH=$(grep -c "\"runner\": \"$EXPECTED\"" "$EVENTS_FILE" 2>/dev/null || echo 0)
    if [ "$MATCH" -gt 0 ]; then
        echo "Runner: ✅ $EXPECTED confirmed ($MATCH events)"
    else
        echo "Runner: ❌ $EXPECTED not confirmed"
    fi
fi

# Game check
echo ""
echo "--- Game ---"
INDEX=$(find . -maxdepth 2 -name "index.html" -not -path "./.factory/*" 2>/dev/null | head -1)
if [ -n "$INDEX" ]; then
    SIZE=$(wc -c < "$INDEX" | tr -d ' ')
    echo "✅ index.html ($SIZE bytes)"
    for check in \
        "canvas:Canvas" \
        "addEventListener:Events" \
        "localStorage:High score" \
        "keydown:Keyboard" \
        "setInterval\|requestAnimationFrame:Game loop" \
        "score:Score"; do
        PAT="${check%%:*}"; LABEL="${check##*:}"
        if grep -qi "$PAT" "$INDEX" 2>/dev/null; then
            echo "  ✅ $LABEL"
        else
            echo "  ❌ $LABEL"
        fi
    done
    echo ""
    echo "Play: open $(pwd)/$INDEX"
else
    echo "❌ index.html not built"
fi

# Experiments
echo ""
echo "--- Experiments ---"
find . -name "results.tsv" -not -path "./.factory/worktrees/*" 2>/dev/null | head -1 | xargs cat 2>/dev/null || echo "(none)"

echo ""
echo "Logs: $(pwd)/.factory/"
