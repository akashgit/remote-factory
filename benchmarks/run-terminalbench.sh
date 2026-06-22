#!/usr/bin/env bash
set -euo pipefail

# benchmarks/run-terminalbench.sh — Standalone CI pipeline for TerminalBench.
# Thin wrapper around Harbor, which handles the entire lifecycle:
# container orchestration, agent execution, verification, and scoring.

# ── Shared library ──

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# ── Configuration ──

TASK_NAME="${1:-regex-chess}"
SOLVER_TIMEOUT="${2:-1800}"

BENCHMARK="terminalbench"
INSTANCE_ID="${TASK_NAME}"
RUN_ID="ci-terminalbench-${TIMESTAMP}"
RESULT_FILE="${CI_RESULTS_DIR}/${TIMESTAMP}-terminalbench.json"

JOBS_DIR=""

PASSED=0
RESOLVED=0
TOTAL=1

# ── Helpers ──

cleanup() {
    local exit_code=$?
    if [ -n "${JOBS_DIR}" ] && [ -d "${JOBS_DIR}" ]; then
        if [ "${PRESERVE_WORKSPACE:-}" = "1" ]; then
            log "Preserving harbor jobs at ${JOBS_DIR} (PRESERVE_WORKSPACE=1)"
        else
            log "Cleaning up harbor jobs directory"
            rm -rf "${JOBS_DIR}"
        fi
    fi
    PASSED="${RESOLVED}"
    DETAILS_JSON='{"solver": "'"${BENCHMARK_SOLVER:-factory}"'", "cost_usd": '"${COST_USD:-0}"', "input_tokens": '"${INPUT_TOKENS:-0}"', "output_tokens": '"${OUTPUT_TOKENS:-0}"', "cache_read_tokens": '"${CACHE_READ_TOKENS:-0}"', "cache_creation_tokens": '"${CACHE_CREATION_TOKENS:-0}"'}'
    write_result
    if [ "${STATUS}" = "success" ]; then
        exit 0
    else
        exit "${exit_code:-1}"
    fi
}

trap cleanup EXIT

# ── Step 1: Parse and display configuration ──

show_banner "TerminalBench"
log "Step 1: Configuration"
echo "    Task name:       ${TASK_NAME}"
echo "    Solver timeout:  ${SOLVER_TIMEOUT}s ($(( SOLVER_TIMEOUT / 3600 ))h $(( (SOLVER_TIMEOUT % 3600) / 60 ))m)"
echo "    Run ID:          ${RUN_ID}"
echo "    Timestamp:       ${TIMESTAMP}"
echo ""

# ── Step 2: Validate prerequisites ──

log "Step 2: Validating prerequisites"

MISSING=()

if ! command -v docker &>/dev/null && [ ! -x /usr/bin/docker ]; then
    MISSING+=("docker (install from https://docs.docker.com/get-docker/)")
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "    ERROR: Missing prerequisites:"
    for m in "${MISSING[@]}"; do
        echo "      - ${m}"
    done
    exit 1
fi

echo "    docker: found"

ensure_uvx

echo "    harbor: checking availability via uvx..."
if ! uvx harbor --version &>/dev/null 2>&1; then
    echo "    harbor: installing via uvx..."
    uvx harbor --version || {
        echo "    ERROR: Failed to install/run harbor via uvx"
        exit 1
    }
fi
echo "    harbor: available"

# API key configuration — Harbor's claude-code agent needs API access
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    echo "    ANTHROPIC_API_KEY: set"
else
    setup_vertex_env
    if [ -n "${ANTHROPIC_VERTEX_PROJECT_ID:-}" ]; then
        echo "    Vertex AI: configured (project: ${ANTHROPIC_VERTEX_PROJECT_ID})"
    else
        echo "    WARNING: No ANTHROPIC_API_KEY or Vertex AI configuration found."
        echo "    Harbor's claude-code agent requires API access."
    fi
fi

echo "    All prerequisites satisfied."
echo ""

# ── Step 3: Run Harbor evaluation ──

log "Step 3: Running Harbor evaluation"

JOBS_DIR="$(mktemp -d /tmp/terminalbench-jobs-XXXXXX)"
echo "    Jobs directory: ${JOBS_DIR}"
echo "    Started at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Agent timeout multiplier scales the per-task timeout from task.toml.
# Default task timeout is typically 120s; multiplier adjusts to our desired solver timeout.
TIMEOUT_MULTIPLIER=$(( SOLVER_TIMEOUT / 120 ))
[ "${TIMEOUT_MULTIPLIER}" -lt 1 ] && TIMEOUT_MULTIPLIER=1

MODEL="anthropic/claude-opus-4-6"

echo "    Model:           ${MODEL}"
echo "    Timeout mult:    ${TIMEOUT_MULTIPLIER}x"
echo "    Task:            ${TASK_NAME}"
echo ""

cd "${HARNESS_DIR}"

HARBOR_EXIT=0

if [ "${BENCHMARK_SOLVER:-factory}" = "claude-code" ]; then
    # Use Harbor's built-in claude-code agent
    AGENT_ARGS=(--agent claude-code --extra-instruction-path "${HARNESS_DIR}/benchmarks/terminalbench-extra-instructions.md")
    echo "    Agent:           claude-code (Harbor built-in + extra instructions)"
else
    # Use Factory Harbor agent
    AGENT_MODULE="${HARNESS_DIR}/benchmarks/factory_harbor_agent.py"
    export PYTHONPATH="$(dirname "${AGENT_MODULE}"):${PYTHONPATH:-}"
    AGENT_ARGS=(--agent-import-path factory_harbor_agent:FactoryCeo)
    echo "    Agent:           factory (FactoryCeo)"
fi

if [ -n "${ANTHROPIC_VERTEX_PROJECT_ID:-}" ]; then
    GCLOUD_ADC="${GOOGLE_APPLICATION_CREDENTIALS:-${HOME}/.config/gcloud/application_default_credentials.json}"
    echo "    Auth mode:       Vertex AI (project: ${ANTHROPIC_VERTEX_PROJECT_ID})"
    uvx harbor run \
        --dataset terminal-bench@2.0 \
        "${AGENT_ARGS[@]}" \
        --model "${MODEL}" \
        --include-task-name "${TASK_NAME}" \
        --n-concurrent 1 \
        --jobs-dir "${JOBS_DIR}" \
        --agent-timeout-multiplier "${TIMEOUT_MULTIPLIER}" \
        --ae "CLAUDE_CODE_USE_VERTEX=1" \
        --ae "ANTHROPIC_VERTEX_PROJECT_ID=${ANTHROPIC_VERTEX_PROJECT_ID}" \
        --ae "CLOUD_ML_REGION=${CLOUD_ML_REGION:-us-east5}" \
        --ae "ANTHROPIC_MODEL=${ANTHROPIC_MODEL:-claude-opus-4-6[1m]}" \
        --ae "GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcloud-adc.json" \
        --ae "CLAUDE_CODE_SUBAGENT_MODEL=${CLAUDE_CODE_SUBAGENT_MODEL:-claude-opus-4-6[1m]}" \
        --ae "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=${CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS:-1}" \
        --ae "ANTHROPIC_DEFAULT_OPUS_MODEL=${ANTHROPIC_DEFAULT_OPUS_MODEL:-claude-opus-4-6[1m]}" \
        --ae "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=${CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING:-1}" \
        --ae "MAX_THINKING_TOKENS=${MAX_THINKING_TOKENS:-128000}" \
        --ae "CLAUDE_CODE_EFFORT_LEVEL=${CLAUDE_CODE_EFFORT_LEVEL:-XHIGH}" \
        --mounts '[{"type": "bind", "source": "'"${GCLOUD_ADC}"'", "target": "/tmp/gcloud-adc.json", "read_only": true}]' \
        2>&1 || HARBOR_EXIT=$?
else
    echo "    Auth mode:       Direct API (ANTHROPIC_API_KEY)"
    uvx harbor run \
        --dataset terminal-bench@2.0 \
        "${AGENT_ARGS[@]}" \
        --model "${MODEL}" \
        --include-task-name "${TASK_NAME}" \
        --n-concurrent 1 \
        --jobs-dir "${JOBS_DIR}" \
        --agent-timeout-multiplier "${TIMEOUT_MULTIPLIER}" \
        --ae "ANTHROPIC_MODEL=${ANTHROPIC_MODEL:-claude-opus-4-6[1m]}" \
        --ae "CLAUDE_CODE_SUBAGENT_MODEL=${CLAUDE_CODE_SUBAGENT_MODEL:-claude-opus-4-6[1m]}" \
        --ae "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=${CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS:-1}" \
        --ae "ANTHROPIC_DEFAULT_OPUS_MODEL=${ANTHROPIC_DEFAULT_OPUS_MODEL:-claude-opus-4-6[1m]}" \
        --ae "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=${CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING:-1}" \
        --ae "MAX_THINKING_TOKENS=${MAX_THINKING_TOKENS:-128000}" \
        --ae "CLAUDE_CODE_EFFORT_LEVEL=${CLAUDE_CODE_EFFORT_LEVEL:-XHIGH}" \
        2>&1 || HARBOR_EXIT=$?
fi

if [ "${HARBOR_EXIT}" -ne 0 ]; then
    echo "    Harbor exited with code ${HARBOR_EXIT}"
fi

# Temporarily allow failures — cost/reward extraction uses grep/find which return
# non-zero on no match; pipefail would kill the script before reaching STATUS=success.
set +e

# Extract cost from Harbor result
COST_USD=0
INPUT_TOKENS=0
OUTPUT_TOKENS=0
CACHE_READ_TOKENS=0
CACHE_CREATION_TOKENS=0

HARBOR_RESULT=$(find "${JOBS_DIR}" -name 'result.json' -maxdepth 2 2>/dev/null | head -1)
if [ -n "${HARBOR_RESULT}" ]; then
    COST_DATA=$(python3 -c "
import json
with open('${HARBOR_RESULT}') as f:
    data = json.load(f)
cost = 0
for trial in data.get('trials', {}).values():
    cost += trial.get('cost_usd', 0) or 0
print(f'COST_USD={cost}')
" 2>/dev/null)
    eval "${COST_DATA}" 2>/dev/null || true
fi

if [ "${COST_USD}" = "0" ] || [ -z "${COST_USD}" ]; then
    AGENT_LOG=$(find "${JOBS_DIR}" -name 'claude-code.txt' -o -name 'claude_code_stream_output.jsonl' -o -name 'factory-ceo.txt' 2>/dev/null | head -1)
    if [ -n "${AGENT_LOG}" ]; then
        COST_DATA=$(grep 'total_cost_usd' "${AGENT_LOG}" 2>/dev/null | tail -1 | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        data = json.loads(line.strip())
        if 'total_cost_usd' in data:
            print(f'COST_USD={data[\"total_cost_usd\"]}')
            u = data.get('usage', {})
            print(f'INPUT_TOKENS={u.get(\"input_tokens\", 0)}')
            print(f'OUTPUT_TOKENS={u.get(\"output_tokens\", 0)}')
    except: pass
" 2>/dev/null || true)
        eval "${COST_DATA}" 2>/dev/null || true
    fi
fi

echo "    Finished at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# ── Step 4: Extract and report results ──

log "Step 4: Extracting results"

# Harbor writes reward files inside its jobs directory.
# Path pattern: jobs/<name>/trials/<task>/attempt_<n>/logs/verifier/reward.txt
# Search for reward.json first (multi-metric), then reward.txt (single score).
REWARD_FILE=""

for candidate in $(find "${JOBS_DIR}" -name 'reward.json' 2>/dev/null); do
    if [ -f "${candidate}" ]; then
        REWARD_FILE="${candidate}"
        break
    fi
done

if [ -z "${REWARD_FILE}" ]; then
    for candidate in $(find "${JOBS_DIR}" -name 'reward.txt' 2>/dev/null); do
        if [ -f "${candidate}" ]; then
            REWARD_FILE="${candidate}"
            break
        fi
    done
fi

if [ -n "${REWARD_FILE}" ] && [ -f "${REWARD_FILE}" ]; then
    echo "    Reward file: ${REWARD_FILE}"

    if [[ "${REWARD_FILE}" == *.json ]]; then
        eval "$(python3 -c "
import json
with open('${REWARD_FILE}') as f:
    data = json.load(f)
if isinstance(data, dict):
    values = [v for v in data.values() if isinstance(v, (int, float))]
    score = sum(values) / len(values) if values else 0.0
    resolved = 1 if score > 0.5 else 0
elif isinstance(data, (int, float)):
    resolved = 1 if float(data) > 0.5 else 0
else:
    resolved = 0
print(f'RESOLVED={resolved}')
print(f'TOTAL=1')
")"
    else
        REWARD_VALUE="$(cat "${REWARD_FILE}" | tr -d '[:space:]')"
        echo "    Reward value: ${REWARD_VALUE}"
        if [ "${REWARD_VALUE}" = "1" ] || [ "${REWARD_VALUE}" = "1.0" ]; then
            RESOLVED=1
        else
            RESOLVED=0
        fi
        TOTAL=1
    fi
else
    # Fallback: search for summary/results files
    SUMMARY_FILE=""
    for candidate in $(find "${JOBS_DIR}" -name 'results*.json' -o -name 'summary*.json' 2>/dev/null); do
        if [ -f "${candidate}" ]; then
            SUMMARY_FILE="${candidate}"
            break
        fi
    done

    if [ -n "${SUMMARY_FILE}" ] && [ -f "${SUMMARY_FILE}" ]; then
        echo "    Summary file: ${SUMMARY_FILE}"
        eval "$(python3 -c "
import json
with open('${SUMMARY_FILE}') as f:
    data = json.load(f)
resolved = 0
total = 1
if isinstance(data, dict):
    if 'reward' in data:
        resolved = 1 if float(data['reward']) > 0.5 else 0
    elif 'score' in data:
        resolved = 1 if float(data['score']) > 0.5 else 0
    elif 'results' in data:
        results = data['results']
        if isinstance(results, dict):
            total = len(results)
            resolved = sum(1 for v in results.values()
                         if isinstance(v, dict) and v.get('reward', 0) > 0.5)
        elif isinstance(results, list):
            total = len(results)
            resolved = sum(1 for v in results
                         if isinstance(v, dict) and v.get('reward', 0) > 0.5)
print(f'RESOLVED={resolved}')
print(f'TOTAL={max(total, 1)}')
")"
    else
        echo "    No results files found. Marking as unresolved."
        echo "    Contents of jobs directory:"
        find "${JOBS_DIR}" -type f 2>/dev/null | head -20 || echo "      (empty)"
        RESOLVED=0
        TOTAL=1
    fi
fi

echo ""
echo "============================================"
if [ "${RESOLVED}" -gt 0 ]; then
    echo "  Result: RESOLVED (${RESOLVED}/${TOTAL})"
else
    echo "  Result: NOT RESOLVED (${RESOLVED}/${TOTAL})"
fi
echo "============================================"
echo ""

set -e

STATUS="success"

# cleanup trap will write the final result JSON and exit 0
