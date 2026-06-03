#!/usr/bin/env bash
#
# Real factory e2e test: run the FULL factory CEO loop with codex as the
# runner for ALL agents to build a browser-based Snake game (贪吃蛇).
#
# This goes through the complete factory pipeline, ALL on codex:
#   CEO (codex) orchestrates →
#     Researcher (codex) studies project →
#     Strategist (codex) plans hypothesis →
#     Evaluator (codex) baseline score →
#     Builder (codex) implements →
#     Reviewer (codex) quality check →
#     Evaluator (codex) after score →
#     CEO keep/revert decision →
#     Archivist (codex) records learnings
#
# Usage:
#   ./scripts/test_codex_build_game.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FACTORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load API key from .env if present
if [ -f "$FACTORY_ROOT/.env" ]; then
    set -a
    source "$FACTORY_ROOT/.env"
    set +a
fi

# Use a cheap/fast model (override with MODEL env var)
MODEL="${MODEL:-gpt-5.4-mini}"

# Use the local worktree's factory, not the globally installed one.
FACTORY="uv run --project $FACTORY_ROOT factory"

# ── Preflight ──────────────────────────────────────────────────
echo "=== Preflight ==="
command -v codex >/dev/null  || { echo "FAIL: codex not found on PATH"; exit 1; }
echo "  codex:   $(which codex) ($(codex --version 2>&1 | head -1))"
echo "  factory: $FACTORY_ROOT (local worktree via uv run)"
echo "  runner:  codex for ALL agents (CEO + specialists)"

if [ -f "$HOME/.codex/auth.json" ]; then
    echo "  codex auth: file-based (~/.codex/auth.json)"
elif [ -n "${CODEX_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ]; then
    echo "  codex auth: env var"
else
    echo "FAIL: no codex auth. Run 'codex login' or set CODEX_API_KEY"
    exit 1
fi

# ── Create a fresh project dir ─────────────────────────────────
PROJECT_DIR=$(mktemp -d -t factory-codex-snake-XXXXXX)
echo ""
echo "=== Project: $PROJECT_DIR ==="

cd "$PROJECT_DIR"
git init -q
git config user.email "test@factory.dev"
git config user.name "Factory Test"

# Seed the project with a factory.md spec so the CEO knows what to build
cat > factory.md <<'SPEC'
# Snake Game

## Goal
Build a browser-based Snake game with a polished UI.

## Scope
- index.html
- Any supporting files

## Constraints
- Single self-contained HTML file with embedded CSS and JS
- No external dependencies or CDN imports
- Must work by opening index.html directly in a browser

## Requirements
- 400x400 pixel canvas, dark theme background #1a1a2e
- Snake: bright green #00ff41 with depth shading on segments
- Food: red circle #ff0040
- Subtle grid lines
- Score display above canvas in large font
- Title showing both Chinese and English name at top
- Game-over overlay with final score and restart instruction
- Arrow keys to control, cannot reverse into self
- Snake wraps around edges
- Eating food grows snake and increments score
- Game over on self-collision
- Speed increases every 5 points
- SPACE to restart after game over
- Starts automatically on page load
- requestAnimationFrame game loop with tick counter
- 20x20 grid, snake as array of {x,y} segments

## Eval
The game should open in a browser and be immediately playable.
SPEC

git add . && git commit -q -m "init: snake game spec"

# ── Run full factory CEO loop ──────────────────────────────────
echo ""
echo "================================================================"
echo "  FACTORY CEO RUN — ALL CODEX"
echo "  Runner: codex (CEO + all specialist agents)"
echo "  Model:  $MODEL"
echo "  Mode:   build (new project from spec)"
echo "================================================================"
echo ""

START_TIME=$(date +%s)

# FACTORY_RUNNER=codex makes ALL agents use codex — including the CEO.
# The CEO shell-outs to `factory agent <role>` which inherits FACTORY_RUNNER,
# so specialists also run on codex.
export FACTORY_RUNNER=codex
export FACTORY_RUNNER_QUIET=0

# The CEO on codex captures sub-agent output inside its sandbox — it doesn't
# reach our terminal in real time. So we tail the events log in the background
# to see agent starts/completions as they happen.
EVENTS_LOG="$PROJECT_DIR/.factory/events.jsonl"

# Start event watcher in background (will be killed when script ends)
(
    # Wait for events file to appear
    while [ ! -f "$EVENTS_LOG" ]; do sleep 1; done
    tail -f "$EVENTS_LOG" 2>/dev/null | while IFS= read -r line; do
        agent=$(echo "$line" | python3 -c "import sys,json; e=json.loads(sys.stdin.read()); print(f'  [event] {e.get(\"type\",\"?\"):25s} agent={e.get(\"agent\",\"-\"):12s} {str(e.get(\"data\",{}))[:120]}')" 2>/dev/null)
        [ -n "$agent" ] && echo "$agent"
    done
) &
EVENT_WATCHER_PID=$!
trap "kill $EVENT_WATCHER_PID 2>/dev/null" EXIT

$FACTORY ceo "$PROJECT_DIR" --runner codex --model "$MODEL" --headless 2>&1 | while IFS= read -r line; do
    echo "  [factory] $line"
done

kill $EVENT_WATCHER_PID 2>/dev/null || true

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo ""
echo "=== Factory run completed in ${ELAPSED}s ==="

# ── Post-run analysis ─────────────────────────────────────────
echo ""
echo "=== Post-run analysis ==="

# Check events
if [ -f "$PROJECT_DIR/.factory/events.jsonl" ]; then
    echo ""
    echo "  --- Agent events ---"
    python3 -c "
import json, sys
events = [json.loads(l) for l in open('$PROJECT_DIR/.factory/events.jsonl')]
for e in events:
    t = e.get('type', '?')
    agent = e.get('agent', '')
    ts = e.get('timestamp', '')[:19]
    data = e.get('data', {})
    if t in ('agent.started', 'agent.completed', 'agent.failed'):
        status = 'STARTED' if 'started' in t else 'DONE' if 'completed' in t else 'FAIL'
        tokens = data.get('output_tokens', '')
        info = f'tokens_out={tokens}' if tokens else data.get('task', '')[:80]
        print(f'    {ts}  {status:7s}  {agent:12s}  {info}')
    elif t in ('experiment.begin', 'eval.completed', 'phase.build.completed', 'sprint.completed'):
        detail = json.dumps(data)[:100]
        print(f'    {ts}  {t:30s}  {detail}')
" 2>/dev/null || echo "    (could not parse events)"
fi

# Check experiment results
if [ -f "$PROJECT_DIR/.factory/results.tsv" ]; then
    echo ""
    echo "  --- Experiment results ---"
    cat "$PROJECT_DIR/.factory/results.tsv" | head -5 | sed 's/^/    /'
fi

# Check what was built
echo ""
echo "  --- Files in project ---"
find "$PROJECT_DIR" -maxdepth 2 -name "*.html" -o -name "*.js" -o -name "*.css" 2>/dev/null | sed 's/^/    /'

# Look for the game file
GAME_FILE=""
if [ -f "$PROJECT_DIR/index.html" ]; then
    GAME_FILE="$PROJECT_DIR/index.html"
else
    # Check worktrees — factory ceo creates a worktree
    GAME_FILE=$(find "$PROJECT_DIR/.factory/worktrees" -name "index.html" -type f 2>/dev/null | head -1)
fi

if [ -n "$GAME_FILE" ] && [ -f "$GAME_FILE" ]; then
    LINES=$(wc -l < "$GAME_FILE")
    SIZE=$(wc -c < "$GAME_FILE")
    echo ""
    echo "  --- Game file: $GAME_FILE ---"
    echo "    lines: $LINES"
    echo "    bytes: $SIZE"

    # Structural checks
    echo ""
    echo "  --- Structural checks ---"
    PASS=0; TOTAL=0
    for pattern in "<canvas" "addEventListener" "score" "requestAnimationFrame" "Snake"; do
        TOTAL=$((TOTAL + 1))
        if grep -q "$pattern" "$GAME_FILE"; then
            echo "    [pass] $pattern"
            PASS=$((PASS + 1))
        else
            echo "    [FAIL] $pattern"
        fi
    done
    echo "    $PASS / $TOTAL passed"

    echo ""
    echo "=========================================="
    echo "  RESULT: FACTORY RUN COMPLETE"
    echo ""
    echo "  Runner:  codex (ALL agents including CEO)"
    echo "  Built:   $(basename $GAME_FILE) ($LINES lines)"
    echo "  Time:    ${ELAPSED}s"
    echo "  Project: $PROJECT_DIR"
    echo "=========================================="
    echo ""
    echo "  Opening in browser..."
    open "$GAME_FILE" 2>/dev/null || xdg-open "$GAME_FILE" 2>/dev/null || true
    echo "    $GAME_FILE"
else
    echo ""
    echo "=========================================="
    echo "  RESULT: NO GAME FILE FOUND"
    echo "  Check the factory logs above for errors."
    echo "  Project: $PROJECT_DIR"
    echo "=========================================="
    echo ""
    echo "  Directory tree:"
    find "$PROJECT_DIR" -maxdepth 3 -not -path '*/\.git/*' | head -30 | sed 's/^/    /'
    exit 1
fi
