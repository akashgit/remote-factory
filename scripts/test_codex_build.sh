#!/usr/bin/env bash
# Test the FULL factory loop with Codex as the agent runner.
#
# This runs `factory ceo --runner codex` which spawns the CEO agent
# (as a codex subprocess) which then orchestrates the full loop:
#   CEO → Researcher → Strategist → Builder → Reviewer → Evaluator → Archivist
# ALL running as codex subprocesses.
#
# Usage: ./scripts/test_codex_build.sh
#
set -euo pipefail

# Use the local worktree's factory, not the globally installed one
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FACTORY="uv run --project $SCRIPT_DIR factory"

WORKDIR=$(mktemp -d)/snake-game
mkdir -p "$WORKDIR"
trap 'echo ""; echo "=== Project preserved at: $WORKDIR ===" ' EXIT

echo "=== Setting up project in $WORKDIR ==="
echo "=== Using factory from: $SCRIPT_DIR ==="
cd "$WORKDIR"
git init -q
git commit --allow-empty -q -m "init"
mkdir -p .factory

# Write a factory.md spec for the CEO to discover and build
cat > factory.md <<'SPEC'
# Snake Game (贪吃蛇)

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
git commit -q -m "add factory.md spec"

echo ""
echo "=== Running: factory ceo --runner codex --headless ==="
echo "=== This runs the FULL factory loop: CEO → agents, all powered by Codex ==="
echo ""

$FACTORY ceo "$WORKDIR" \
  --runner codex \
  --headless \
  --timeout 600 2>&1 | tee "$WORKDIR/.factory/ceo-stdout.log"

CEO_EXIT=${PIPESTATUS[0]}

echo ""
echo "=========================================="
echo "=== RESULTS ==="
echo "=========================================="
echo ""

# 1. Exit code
echo "1. CEO exit code: $CEO_EXIT"

# 2. Events — verify codex runner was used for ALL agents
echo ""
echo "2. Agent events (runner identification):"
if [ -f "$WORKDIR/.factory/events.jsonl" ]; then
  CODEX_COUNT=0
  NON_CODEX_COUNT=0
  while IFS= read -r line; do
    TYPE=$(echo "$line" | python3 -c "import sys,json; print(json.load(sys.stdin).get('type','?'))" 2>/dev/null || echo "?")
    AGENT=$(echo "$line" | python3 -c "import sys,json; print(json.load(sys.stdin).get('agent','?'))" 2>/dev/null || echo "?")
    RUNNER=$(echo "$line" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('runner','?'))" 2>/dev/null || echo "?")
    TS=$(echo "$line" | python3 -c "import sys,json; t=json.load(sys.stdin).get('timestamp',''); print(t[11:19] if len(t)>19 else t)" 2>/dev/null || echo "?")
    if echo "$TYPE" | grep -q "started\|completed\|failed"; then
      echo "   $TS  $TYPE  agent=$AGENT  runner=$RUNNER"
    fi
    if [ "$RUNNER" = "codex" ]; then
      CODEX_COUNT=$((CODEX_COUNT + 1))
    elif [ "$RUNNER" != "?" ] && echo "$TYPE" | grep -q "started"; then
      NON_CODEX_COUNT=$((NON_CODEX_COUNT + 1))
    fi
  done < "$WORKDIR/.factory/events.jsonl"
  echo ""
  if [ "$CODEX_COUNT" -gt 0 ] && [ "$NON_CODEX_COUNT" -eq 0 ]; then
    echo "   ✅ ALL agents used Codex runner ($CODEX_COUNT events)"
  elif [ "$CODEX_COUNT" -gt 0 ]; then
    echo "   ⚠️  Mixed runners: $CODEX_COUNT codex, $NON_CODEX_COUNT other"
  else
    echo "   ❌ Could not confirm Codex runner in events"
  fi
else
  echo "   (no events file found — check .factory/ in the worktree)"
  # Events might be in a worktree subfolder
  find "$WORKDIR" -name "events.jsonl" 2>/dev/null | while read f; do
    echo "   Found events at: $f"
    head -5 "$f"
  done
fi

# 3. Which agents were spawned?
echo ""
echo "3. Agent reviews saved:"
if [ -d "$WORKDIR/.factory/reviews" ]; then
  ls -la "$WORKDIR/.factory/reviews/" 2>/dev/null
else
  echo "   (no reviews dir)"
  find "$WORKDIR" -path "*/.factory/reviews/*" -name "*-latest.md" 2>/dev/null | while read f; do
    echo "   Found: $f"
  done
fi

# 4. Was anything built?
echo ""
echo "4. Project files:"
find "$WORKDIR" -maxdepth 2 -name "*.html" -o -name "*.py" -o -name "*.js" 2>/dev/null | while read f; do
  SIZE=$(wc -c < "$f" | tr -d ' ')
  echo "   $f ($SIZE bytes)"
done

# 5. Check for index.html
echo ""
echo "5. Game validation:"
INDEX=$(find "$WORKDIR" -maxdepth 2 -name "index.html" 2>/dev/null | head -1)
if [ -n "$INDEX" ]; then
  SIZE=$(wc -c < "$INDEX" | tr -d ' ')
  echo "   index.html found: $INDEX ($SIZE bytes)"

  CHECKS=(
    "canvas:Canvas element"
    "addEventListener:Event listeners"
    "localStorage:High score persistence"
    "keydown:Keyboard controls"
    "requestAnimationFrame\|setInterval:Game loop"
    "gameOver\|game_over\|GAME.OVER\|game-over:Game over logic"
    "score:Score tracking"
  )

  for check in "${CHECKS[@]}"; do
    PATTERN="${check%%:*}"
    LABEL="${check##*:}"
    if grep -qi "$PATTERN" "$INDEX" 2>/dev/null; then
      echo "   ✅ $LABEL"
    else
      echo "   ❌ $LABEL (not found)"
    fi
  done
  echo ""
  echo "   To play: open $INDEX"
else
  echo "   ❌ No index.html found"
fi

# 6. Factory experiment history
echo ""
echo "6. Experiment history:"
if [ -f "$WORKDIR/.factory/results.tsv" ]; then
  cat "$WORKDIR/.factory/results.tsv"
else
  echo "   (no results.tsv)"
  find "$WORKDIR" -name "results.tsv" 2>/dev/null | while read f; do
    echo "   Found: $f"
    cat "$f"
  done
fi

echo ""
echo "7. Logs:"
echo "   CEO stdout:    $WORKDIR/.factory/ceo-stdout.log"
echo "   Events:        $WORKDIR/.factory/events.jsonl"
echo "   Reviews:       $WORKDIR/.factory/reviews/"
echo "   Experiments:   $WORKDIR/.factory/experiments/"
