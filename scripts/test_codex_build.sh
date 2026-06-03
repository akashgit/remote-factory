#!/usr/bin/env bash
# Test the factory with Codex as the agent runner.
# Builds a fizzbuzz game in a temp directory, runs it, and shows the full log.
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
echo "=== Running: factory agent builder --runner codex ==="
echo ""

$FACTORY agent builder \
  --task "Create a single file called fizzbuzz.py in the current directory.
It takes one CLI argument N (integer) via sys.argv.
It prints FizzBuzz from 1 to N, one value per line.
Rules: divisible by 3 prints Fizz, divisible by 5 prints Buzz, both prints FizzBuzz, otherwise the number.
Only create fizzbuzz.py. Do not create any other files. Do not run tests." \
  --project "$WORKDIR" \
  --runner codex \
  --timeout 300 2>&1 | tee "$WORKDIR/.factory/builder-stdout.log"

EXIT_CODE=${PIPESTATUS[0]}

echo ""
echo "=========================================="
echo "=== RESULTS ==="
echo "=========================================="
echo ""

# 1. Factory exit code
echo "1. Factory exit code: $EXIT_CODE"

# 2. Was codex actually used? Check events
echo ""
echo "2. Events log (.factory/events.jsonl):"
if [ -f "$WORKDIR/.factory/events.jsonl" ]; then
  cat "$WORKDIR/.factory/events.jsonl" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || cat "$WORKDIR/.factory/events.jsonl"
else
  echo "   (no events file found)"
fi

# 3. Builder review
echo ""
echo "3. Builder review (.factory/reviews/builder-latest.md):"
if [ -f "$WORKDIR/.factory/reviews/builder-latest.md" ]; then
  head -10 "$WORKDIR/.factory/reviews/builder-latest.md"
  echo "   ... (truncated)"
else
  echo "   (no review file found)"
fi

# 4. Was the file created?
echo ""
echo "4. Files in project:"
ls -la "$WORKDIR"/*.py 2>/dev/null || echo "   (no .py files found)"

# 5. Run fizzbuzz
echo ""
echo "5. Running fizzbuzz.py 15:"
if [ -f "$WORKDIR/fizzbuzz.py" ]; then
  echo "---"
  python3 "$WORKDIR/fizzbuzz.py" 15
  echo "---"

  # Verify correctness
  EXPECTED=$(printf "1\n2\nFizz\n4\nBuzz\nFizz\n7\n8\nFizz\nBuzz\n11\nFizz\n13\n14\nFizzBuzz")
  ACTUAL=$(python3 "$WORKDIR/fizzbuzz.py" 15)
  if [ "$ACTUAL" = "$EXPECTED" ]; then
    echo ""
    echo "   ✅ OUTPUT CORRECT"
  else
    echo ""
    echo "   ❌ OUTPUT WRONG"
    echo "   Expected:"
    echo "$EXPECTED"
    echo "   Got:"
    echo "$ACTUAL"
  fi
else
  echo "   ❌ fizzbuzz.py was not created"
fi

echo ""
echo "6. Full builder stdout log: $WORKDIR/.factory/builder-stdout.log"
echo "   Events log:              $WORKDIR/.factory/events.jsonl"
echo "   Builder review:          $WORKDIR/.factory/reviews/builder-latest.md"
