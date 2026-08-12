#!/usr/bin/env bash
# E2E integration test for the FeatureBench agent adapter.
#
# Prerequisites:
#   - Docker installed and running
#   - ANTHROPIC_API_KEY set in environment
#   - Python 3.11+ available
#
# This script:
#   1. Installs FeatureBench
#   2. Copies the factory adapter into FeatureBench's agent registry
#   3. Creates a config.toml with the API key
#   4. Runs fb infer on 1-2 easy L1 tasks from the lite split
#   5. Validates output.jsonl format
#   6. Runs fb eval on the output
#
# Usage:
#   ANTHROPIC_API_KEY=sk-ant-... ./scripts/run_featurebench_e2e.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== FeatureBench E2E Integration Test ==="
echo ""

# ── Step 0: Check prerequisites ───────────────────────────────────

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo "ERROR: ANTHROPIC_API_KEY is not set."
    echo "Export your API key before running: export ANTHROPIC_API_KEY=sk-ant-..."
    exit 1
fi

if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker is not installed or not in PATH."
    exit 1
fi

if ! docker info &>/dev/null; then
    echo "ERROR: Docker daemon is not running."
    exit 1
fi

echo "[OK] Prerequisites checked"

# ── Step 1: Install FeatureBench ──────────────────────────────────

echo ""
echo "=== Step 1: Installing FeatureBench ==="

pip install featurebench 2>/dev/null || {
    echo "pip install failed, trying from source..."
    TMPDIR=$(mktemp -d)
    git clone https://github.com/LiberCoders/FeatureBench "$TMPDIR/FeatureBench"
    pip install -e "$TMPDIR/FeatureBench"
}

echo "[OK] FeatureBench installed"

# ── Step 2: Register factory agent ────────────────────────────────

echo ""
echo "=== Step 2: Registering factory agent ==="

# Find the FeatureBench agents directory
FB_AGENTS_DIR=$(python -c "
import featurebench.infer.agents as agents_pkg
import os
print(os.path.dirname(agents_pkg.__file__))
")

cp "$PROJECT_ROOT/factory/featurebench/agent.py" "$FB_AGENTS_DIR/factory.py"
echo "[OK] Factory agent copied to $FB_AGENTS_DIR/factory.py"

# Add import to agents __init__.py if not already present
if ! grep -q "FactoryAgent" "$FB_AGENTS_DIR/__init__.py" 2>/dev/null; then
    echo "" >> "$FB_AGENTS_DIR/__init__.py"
    echo "from featurebench.infer.agents.factory import FactoryAgent" >> "$FB_AGENTS_DIR/__init__.py"
    echo "[OK] FactoryAgent registered in agents __init__.py"
else
    echo "[OK] FactoryAgent already registered"
fi

# ── Step 3: Create config.toml ────────────────────────────────────

echo ""
echo "=== Step 3: Creating config.toml ==="

WORKDIR=$(mktemp -d)
cat > "$WORKDIR/config.toml" <<EOF
[env_vars]

[infer]
timeout = 7200
n_concurrent = 1

[infer_config.factory]
ANTHROPIC_API_KEY = "$ANTHROPIC_API_KEY"
FACTORY_RUNNER = "claude"
EOF

echo "[OK] Config written to $WORKDIR/config.toml"

# ── Step 4: Run fb infer on lite split ────────────────────────────

echo ""
echo "=== Step 4: Running fb infer ==="
echo "Using lite split for quick validation..."

cd "$WORKDIR"

# Run on the lite split (smallest set of tasks)
fb infer \
    --config-path "$WORKDIR/config.toml" \
    --agent factory \
    --split lite \
    --timeout 7200 \
    2>&1 | tee "$WORKDIR/infer_output.log"

echo "[OK] fb infer completed"

# ── Step 5: Validate output format ────────────────────────────────

echo ""
echo "=== Step 5: Validating output.jsonl ==="

# Find the most recent run directory
RUN_DIR=$(ls -td "$WORKDIR/runs/"* 2>/dev/null | head -1)

if [ -z "$RUN_DIR" ]; then
    echo "ERROR: No run directory found under $WORKDIR/runs/"
    exit 1
fi

python3 -c "
import json
import sys

output_file = '$RUN_DIR/output.jsonl'
try:
    with open(output_file) as f:
        entries = [json.loads(line) for line in f if line.strip()]
except FileNotFoundError:
    print(f'ERROR: {output_file} not found')
    sys.exit(1)

if not entries:
    print('WARNING: output.jsonl is empty (no tasks completed)')
    sys.exit(0)

errors = []
for i, r in enumerate(entries):
    if 'instance_id' not in r:
        errors.append(f'Entry {i}: missing instance_id')
    if 'model_patch' not in r:
        errors.append(f'Entry {i}: missing model_patch')
    if 'agent' not in r or r['agent'] != 'factory':
        errors.append(f'Entry {i}: wrong agent: {r.get(\"agent\")}')
    if 'success' not in r:
        errors.append(f'Entry {i}: missing success field')

if errors:
    print('VALIDATION ERRORS:')
    for e in errors:
        print(f'  - {e}')
    sys.exit(1)

print(f'[OK] {len(entries)} entries validated')
for r in entries:
    patch_len = len(r.get('model_patch', ''))
    print(f'  {r[\"instance_id\"]}: success={r[\"success\"]}, patch_len={patch_len}')
"

# ── Step 6: Run fb eval ───────────────────────────────────────────

echo ""
echo "=== Step 6: Running fb eval ==="

fb eval --run-dir "$RUN_DIR" 2>&1 | tee "$WORKDIR/eval_output.log"

echo ""
echo "=== E2E Test Complete ==="
echo "Run directory: $RUN_DIR"
echo "Infer log: $WORKDIR/infer_output.log"
echo "Eval log: $WORKDIR/eval_output.log"
