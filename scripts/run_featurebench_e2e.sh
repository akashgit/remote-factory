#!/usr/bin/env bash
# E2E integration test for the FeatureBench agent adapter.
#
# Prerequisites:
#   - Docker installed and running
#   - Auth: ANTHROPIC_API_KEY (direct) OR CLAUDE_CODE_USE_VERTEX (Vertex AI)
#   - Python 3.11+ with `datasets` package (for HuggingFace dataset access)
#
# This script:
#   1. Installs FeatureBench
#   2. Copies the factory adapter into FeatureBench's agent registry
#   3. Creates a config.toml with auth credentials
#   4. Selects 1-2 L1 tasks from the lite split via --task-id (avoids running all ~10)
#   5. Validates output.jsonl format
#   6. Runs fb eval on the output
#
# Usage (direct API key):
#   ANTHROPIC_API_KEY=sk-ant-... ./scripts/run_featurebench_e2e.sh
#
# Usage (Vertex AI):
#   CLAUDE_CODE_USE_VERTEX=1 ANTHROPIC_VERTEX_PROJECT_ID=my-proj \
#     CLOUD_ML_REGION=us-east5 ./scripts/run_featurebench_e2e.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== FeatureBench E2E Integration Test ==="
echo ""

# ── Step 0: Check prerequisites ───────────────────────────────────

if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${CLAUDE_CODE_USE_VERTEX:-}" ]; then
    echo "ERROR: No auth configured."
    echo "Set ANTHROPIC_API_KEY for direct auth, or CLAUDE_CODE_USE_VERTEX=1 for Vertex AI."
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

# Build config.toml with available auth credentials
{
    echo '[env_vars]'
    echo ''
    echo '[infer]'
    echo 'timeout = 7200'
    echo 'n_concurrent = 1'
    echo ''
    echo '[infer_config.factory]'

    if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
        echo "ANTHROPIC_API_KEY = \"$ANTHROPIC_API_KEY\""
    fi

    if [ -n "${CLAUDE_CODE_USE_VERTEX:-}" ]; then
        echo "CLAUDE_CODE_USE_VERTEX = \"${CLAUDE_CODE_USE_VERTEX}\""
        echo "ANTHROPIC_VERTEX_PROJECT_ID = \"${ANTHROPIC_VERTEX_PROJECT_ID:-}\""
        echo "CLOUD_ML_REGION = \"${CLOUD_ML_REGION:-us-east5}\""

        # Read ADC file and pass as env var so it's available inside the container
        ADC_FILE="${GOOGLE_APPLICATION_CREDENTIALS:-$HOME/.config/gcloud/application_default_credentials.json}"
        if [ -f "$ADC_FILE" ]; then
            ADC_CONTENT=$(cat "$ADC_FILE" | tr -d '\n')
            echo "GOOGLE_APPLICATION_CREDENTIALS_JSON = '$ADC_CONTENT'"
            echo "[OK] ADC credentials read from $ADC_FILE">&2
        fi
    fi

    echo 'FACTORY_RUNNER = "claude"'
} > "$WORKDIR/config.toml"

echo "[OK] Config written to $WORKDIR/config.toml"

# ── Step 4: Run fb infer on specific L1 tasks ───────────────────

echo ""
echo "=== Step 4: Running fb infer ==="
echo "Selecting 1-2 L1 tasks from the lite split..."

cd "$WORKDIR"

# Extract the first 2 instance_ids from the lite split to avoid running all ~10 tasks
TASK_IDS=$(python3 -c "
from datasets import load_dataset
ds = load_dataset('LiberCoders/FeatureBench', split='lite')
ids = [row['instance_id'] for row in ds if row.get('level', 0) == 1][:2]
if not ids:
    ids = [row['instance_id'] for row in ds][:2]
print(' '.join(ids))
")

if [ -z "$TASK_IDS" ]; then
    echo "ERROR: Could not extract task IDs from the lite split."
    exit 1
fi

echo "Running on tasks: $TASK_IDS"

# Run on specific tasks only (not the full lite split)
# shellcheck disable=SC2086
fb infer \
    --config-path "$WORKDIR/config.toml" \
    --agent factory \
    --task-id $TASK_IDS \
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
