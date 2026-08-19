#!/usr/bin/env bash
set -euo pipefail

# Run FeatureBench inference + eval for baseline and/or factory agents.
#
# Usage:
#   ./run_comparison.sh --split val --agent claude_code --model claude-sonnet-4-20250514
#   ./run_comparison.sh --split test --both --model claude-sonnet-4-20250514

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SPLIT="val"
AGENT=""
MODEL=""
CONFIG_PATH=""
RUN_BOTH=false
OUTPUT_DIR="${SCRIPT_DIR}/results"
TIMEOUT=7200

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --split train|val|test   Which split to run on (default: val)
  --agent claude_code|factory  Which agent to run
  --model MODEL            Model to use (required)
  --config-path PATH       Path to config.toml (optional)
  --both                   Run both agents sequentially
  --output-dir DIR         Output directory (default: benchmarks/featurebench-splits/results/)
  --timeout N              Per-task timeout in seconds (default: 7200)
  -h, --help               Show this help
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --split) SPLIT="$2"; shift 2 ;;
        --agent) AGENT="$2"; shift 2 ;;
        --model) MODEL="$2"; shift 2 ;;
        --config-path) CONFIG_PATH="$2"; shift 2 ;;
        --both) RUN_BOTH=true; shift ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --timeout) TIMEOUT="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$MODEL" ]]; then
    echo "Error: --model is required"
    exit 1
fi

if [[ "$RUN_BOTH" == false && -z "$AGENT" ]]; then
    echo "Error: --agent or --both is required"
    exit 1
fi

SPLIT_FILE="${SCRIPT_DIR}/${SPLIT}.jsonl"
if [[ ! -f "$SPLIT_FILE" ]]; then
    echo "Error: split file not found: ${SPLIT_FILE}"
    echo "Run generate_splits.py first."
    exit 1
fi

TASK_IDS=$(python3 -c "
import json, sys
ids = []
for line in open('${SPLIT_FILE}'):
    line = line.strip()
    if line:
        ids.append(json.loads(line)['instance_id'])
print(' '.join(ids))
")

echo "Split: ${SPLIT} ($(echo "$TASK_IDS" | wc -w | tr -d ' ') tasks)"
echo "Model: ${MODEL}"
echo "Output: ${OUTPUT_DIR}"

run_agent() {
    local agent_name="$1"
    local agent_output_dir="${OUTPUT_DIR}/${agent_name}"
    mkdir -p "$agent_output_dir"

    echo ""
    echo "=========================================="
    echo "Running agent: ${agent_name}"
    echo "=========================================="

    local task_args=""
    for tid in $TASK_IDS; do
        task_args="${task_args} --task-id ${tid}"
    done

    local config_arg=""
    if [[ -n "$CONFIG_PATH" ]]; then
        config_arg="--config-path ${CONFIG_PATH}"
    fi

    echo "Running fb infer..."
    # shellcheck disable=SC2086
    fb infer \
        --agent "$agent_name" \
        --model "$MODEL" \
        --output-dir "$agent_output_dir" \
        --timeout "$TIMEOUT" \
        $config_arg \
        $task_args

    echo "Running fb eval..."
    fb eval --output-dir "$agent_output_dir"

    echo "Agent ${agent_name} complete. Results in ${agent_output_dir}"
}

if [[ "$RUN_BOTH" == true ]]; then
    run_agent "claude_code"
    run_agent "factory"

    echo ""
    echo "Both agents complete. Compare with:"
    echo "  python3 ${SCRIPT_DIR}/compare_results.py \\"
    echo "    --baseline ${OUTPUT_DIR}/claude_code \\"
    echo "    --factory ${OUTPUT_DIR}/factory"
else
    run_agent "$AGENT"
fi
