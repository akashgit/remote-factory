#!/usr/bin/env bash
# Test the factory with Codex as the agent runner.
# Builds a Snake game (贪吃蛇) with a real UI in a temp directory.
#
# Usage: ./scripts/test_codex_build.sh
#
set -euo pipefail

# Use the local worktree's factory, not the globally installed one
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FACTORY="uv run --project $SCRIPT_DIR factory"

WORKDIR=$(mktemp -d)
trap 'echo ""; echo "=== Temp dir preserved at: $WORKDIR ===" ' EXIT

echo "=== Setting up test project in $WORKDIR ==="
echo "=== Using factory from: $SCRIPT_DIR ==="
cd "$WORKDIR"
git init -q
git commit --allow-empty -q -m "init"
mkdir -p .factory

echo ""
echo "=== Phase 1: Build the Snake game ==="
echo ""

$FACTORY agent builder \
  --task "Build a Snake game (贪吃蛇) as a single-page web app.

Create these files in the project root:
  - index.html (the game)
  - README.md (how to play)

Requirements:
  - Pure HTML/CSS/JavaScript, no frameworks, everything in one index.html file
  - Canvas-based rendering, 400x400 pixel game board
  - Snake starts at center, moving right, length 3
  - Arrow keys to change direction (prevent reversing into yourself)
  - Food spawns at random grid positions (20x20 grid)
  - Eating food grows the snake by 1 and increases score by 10
  - Game over when snake hits wall or itself
  - Display current score and high score (persisted in localStorage)
  - Game over screen with 'Press Space to restart' message
  - Speed increases slightly every 5 food eaten
  - Clean visual style: dark background, green snake, red food, white text
  - Responsive: game board centered on page

Do NOT create any other files. Do NOT use npm or any build tools." \
  --project "$WORKDIR" \
  --runner codex \
  --timeout 600 2>&1 | tee "$WORKDIR/.factory/builder-stdout.log"

BUILD_EXIT=${PIPESTATUS[0]}

echo ""
echo "=== Phase 2: Add features to existing code ==="
echo ""

$FACTORY agent builder \
  --task "Read the existing index.html Snake game and add these features:

1. A pause/resume toggle with the P key — show 'PAUSED' overlay when paused
2. A speed selector before the game starts (Slow / Normal / Fast) using HTML buttons
3. Sound effects using the Web Audio API (no external files):
   - Short blip when eating food
   - Low buzz when game over
4. A trailing gradient effect on the snake body (head is bright green, tail fades to dark green)

Read the existing code first. Modify index.html in place. Do NOT create new files." \
  --project "$WORKDIR" \
  --runner codex \
  --timeout 600 2>&1 | tee -a "$WORKDIR/.factory/builder-stdout.log"

FEATURE_EXIT=${PIPESTATUS[0]}

echo ""
echo "=========================================="
echo "=== RESULTS ==="
echo "=========================================="
echo ""

# 1. Exit codes
echo "1. Build exit code:   $BUILD_EXIT"
echo "   Feature exit code: $FEATURE_EXIT"

# 2. Events — verify codex runner was used
echo ""
echo "2. Events log:"
if [ -f "$WORKDIR/.factory/events.jsonl" ]; then
  CODEX_CONFIRMED=false
  while IFS= read -r line; do
    TYPE=$(echo "$line" | python3 -c "import sys,json; print(json.load(sys.stdin).get('type','?'))" 2>/dev/null || echo "?")
    AGENT=$(echo "$line" | python3 -c "import sys,json; print(json.load(sys.stdin).get('agent','?'))" 2>/dev/null || echo "?")
    RUNNER=$(echo "$line" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('runner','?'))" 2>/dev/null || echo "?")
    echo "   $TYPE  agent=$AGENT  runner=$RUNNER"
    if [ "$RUNNER" = "codex" ]; then
      CODEX_CONFIRMED=true
    fi
  done < "$WORKDIR/.factory/events.jsonl"
  echo ""
  if [ "$CODEX_CONFIRMED" = true ]; then
    echo "   ✅ CONFIRMED: Codex runner was used"
  else
    echo "   ❌ WARNING: Could not confirm Codex runner in events"
  fi
else
  echo "   (no events file found)"
fi

# 3. Files created
echo ""
echo "3. Project files:"
find "$WORKDIR" -maxdepth 1 -type f | while read f; do
  SIZE=$(wc -c < "$f" | tr -d ' ')
  echo "   $(basename "$f") (${SIZE} bytes)"
done

# 4. Validate HTML
echo ""
echo "4. Validation:"
if [ -f "$WORKDIR/index.html" ]; then
  SIZE=$(wc -c < "$WORKDIR/index.html" | tr -d ' ')
  echo "   index.html exists ($SIZE bytes)"

  # Check for key features in the HTML
  CHECKS=(
    "canvas:Canvas element"
    "addEventListener:Event listeners"
    "localStorage:High score persistence"
    "keydown:Keyboard controls"
    "requestAnimationFrame\|setInterval:Game loop"
    "gameOver\|game_over\|GAME.OVER\|game-over:Game over logic"
    "score:Score tracking"
    "Audio\|oscillator\|AudioContext:Sound effects"
    "pause\|PAUSE:Pause feature"
  )

  for check in "${CHECKS[@]}"; do
    PATTERN="${check%%:*}"
    LABEL="${check##*:}"
    if grep -qi "$PATTERN" "$WORKDIR/index.html" 2>/dev/null; then
      echo "   ✅ $LABEL"
    else
      echo "   ❌ $LABEL (not found)"
    fi
  done
else
  echo "   ❌ index.html not created"
fi

if [ -f "$WORKDIR/README.md" ]; then
  echo "   ✅ README.md exists"
else
  echo "   ❌ README.md not created"
fi

# 5. How to play
echo ""
echo "5. To play the game:"
echo "   open $WORKDIR/index.html"

echo ""
echo "6. Logs:"
echo "   Builder stdout:  $WORKDIR/.factory/builder-stdout.log"
echo "   Events:          $WORKDIR/.factory/events.jsonl"
echo "   Reviews:         $WORKDIR/.factory/reviews/builder-latest.md"
