#!/usr/bin/env bash
set -euo pipefail

# benchmarks/run-programbench.sh — Standalone CI pipeline for ProgramBench.
# Thin wrapper around Harbor for the agent solve phase, then runs
# `uvx programbench eval` on the host for the full test-suite evaluation
# (programbench eval spawns its own Docker containers, so it cannot run
# inside the Harbor container).

# ── Shared library ──

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# ── Configuration ──

TASK_NAME="${1:-cmatrix}"
SOLVER_TIMEOUT="${2:-3600}"

BENCHMARK="programbench"
RUN_ID="ci-programbench-${TIMESTAMP}"
RESULT_FILE="${CI_RESULTS_DIR}/${TIMESTAMP}-programbench.json"

# Task-specific mapping
case "${TASK_NAME}" in
    cmatrix)
        INSTANCE_ID="abishekvashok__cmatrix.5c082c6"
        ;;
    *)
        echo "ERROR: Unknown ProgramBench task '${TASK_NAME}'"
        echo "Valid tasks: cmatrix"
        exit 1
        ;;
esac

TASK_DIR="${HARNESS_DIR}/benchmarks/programbench-harbor/${TASK_NAME}"

if [ ! -d "${TASK_DIR}" ]; then
    echo "ERROR: Harbor task directory not found: ${TASK_DIR}"
    exit 1
fi

JOBS_DIR=""
RESULTS_DIR=""

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
    if [ -n "${RESULTS_DIR}" ] && [ -d "${RESULTS_DIR}" ]; then
        if [ "${PRESERVE_WORKSPACE:-}" = "1" ]; then
            log "Preserving results at ${RESULTS_DIR} (PRESERVE_WORKSPACE=1)"
        else
            log "Cleaning up results directory"
            rm -rf "${RESULTS_DIR}"
        fi
    fi
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

show_banner "ProgramBench"
log "Step 1: Configuration"
echo "    Task name:       ${TASK_NAME}"
echo "    Instance ID:     ${INSTANCE_ID}"
echo "    Task directory:  ${TASK_DIR}"
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

echo "    programbench: checking availability via uvx..."
if ! uvx programbench --help &>/dev/null 2>&1; then
    echo "    programbench: will be installed on first use via uvx"
fi
echo "    programbench: ready"

# API key configuration
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    echo "    ANTHROPIC_API_KEY: set"
else
    setup_vertex_env
    if [ -n "${ANTHROPIC_VERTEX_PROJECT_ID:-}" ]; then
        echo "    Vertex AI: configured (project: ${ANTHROPIC_VERTEX_PROJECT_ID})"
    else
        echo "    WARNING: No ANTHROPIC_API_KEY or Vertex AI configuration found."
        echo "    Harbor's agent requires API access."
    fi
fi

echo "    All prerequisites satisfied."
echo ""

# ── Step 3: Run Harbor evaluation (agent solve phase) ──

log "Step 3: Running Harbor agent solve phase"

JOBS_DIR="$(mktemp -d /tmp/programbench-jobs-XXXXXX)"
echo "    Jobs directory: ${JOBS_DIR}"
echo "    Started at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

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
    AGENT_ARGS=(--agent claude-code)
    echo "    Agent:           claude-code (Harbor built-in)"
else
    AGENT_MODULE="${HARNESS_DIR}/benchmarks/factory_harbor_agent.py"
    export PYTHONPATH="$(dirname "${AGENT_MODULE}"):${PYTHONPATH:-}"
    AGENT_ARGS=(--agent-import-path factory_harbor_agent:ProgramBenchFactoryCeo)
    echo "    Agent:           factory (ProgramBenchFactoryCeo)"
fi

if [ -n "${ANTHROPIC_VERTEX_PROJECT_ID:-}" ]; then
    GCLOUD_ADC="${GOOGLE_APPLICATION_CREDENTIALS:-${HOME}/.config/gcloud/application_default_credentials.json}"
    echo "    Auth mode:       Vertex AI (project: ${ANTHROPIC_VERTEX_PROJECT_ID})"
    uvx harbor run \
        -p "${TASK_DIR}" \
        "${AGENT_ARGS[@]}" \
        --model "${MODEL}" \
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
        --ae "LANGFUSE_HOST=${LANGFUSE_HOST:-}" \
        --ae "LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY:-}" \
        --ae "LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET_KEY:-}" \
        --ae "LANGFUSE_BASE_URL=${LANGFUSE_BASE_URL:-}" \
        --ae "TELEMETRY_PLATFORM=${TELEMETRY_PLATFORM:-}" \
        --mounts '[{"type": "bind", "source": "'"${GCLOUD_ADC}"'", "target": "/tmp/gcloud-adc.json", "read_only": true}]' \
        2>&1 || HARBOR_EXIT=$?
else
    echo "    Auth mode:       Direct API (ANTHROPIC_API_KEY)"
    uvx harbor run \
        -p "${TASK_DIR}" \
        "${AGENT_ARGS[@]}" \
        --model "${MODEL}" \
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
        --ae "LANGFUSE_HOST=${LANGFUSE_HOST:-}" \
        --ae "LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY:-}" \
        --ae "LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET_KEY:-}" \
        --ae "LANGFUSE_BASE_URL=${LANGFUSE_BASE_URL:-}" \
        --ae "TELEMETRY_PLATFORM=${TELEMETRY_PLATFORM:-}" \
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

# ── Step 4: Extract submission from Harbor jobs directory ──

log "Step 4: Extracting submission from Harbor workspace"

RESULTS_DIR="$(mktemp -d /tmp/programbench-results-XXXXXX)"
echo "    Results directory: ${RESULTS_DIR}"

SUBMISSION_FILE=""
for candidate in $(find "${JOBS_DIR}" -name 'submission.tar.gz' 2>/dev/null); do
    if [ -f "${candidate}" ]; then
        SUBMISSION_FILE="${candidate}"
        break
    fi
done

if [ -n "${SUBMISSION_FILE}" ] && [ -f "${SUBMISSION_FILE}" ]; then
    SUBMISSION_SIZE="$(du -h "${SUBMISSION_FILE}" | cut -f1)"
    echo "    Found submission: ${SUBMISSION_FILE} (${SUBMISSION_SIZE})"
    EVAL_DIR="${RESULTS_DIR}/run/${INSTANCE_ID}"
    mkdir -p "${EVAL_DIR}"
    cp "${SUBMISSION_FILE}" "${EVAL_DIR}/submission.tar.gz"
else
    echo "    WARNING: No submission.tar.gz found in Harbor jobs directory"
    echo "    Contents of jobs directory:"
    find "${JOBS_DIR}" -type f 2>/dev/null | head -20 || echo "      (empty)"
fi

echo ""

# ── Step 5: Run ProgramBench evaluation on the host ──

log "Step 5: Running ProgramBench evaluation"

if [ -n "${SUBMISSION_FILE}" ] && [ -f "${RESULTS_DIR}/run/${INSTANCE_ID}/submission.tar.gz" ]; then
    EVAL_EXIT=0
    uvx programbench eval "${RESULTS_DIR}/run" -w 1 -b 4 --docker-cpus 4 --force \
        2>&1 || EVAL_EXIT=$?

    if [ "${EVAL_EXIT}" -ne 0 ]; then
        echo "    WARNING: ProgramBench evaluation exited with code ${EVAL_EXIT}"
    fi
    echo "    Evaluation complete."
else
    echo "    Skipping evaluation — no submission available."
fi

echo ""

# ── Step 6: Extract and report results ──

log "Step 6: Extracting results"

EVAL_JSON="${RESULTS_DIR}/run/${INSTANCE_ID}/${INSTANCE_ID}.eval.json"

if [ -f "${EVAL_JSON}" ]; then
    echo "    Eval file: ${EVAL_JSON}"
    eval "$(python3 -c "
import json
with open('${EVAL_JSON}') as f:
    data = json.load(f)
results = data.get('test_results', [])
passed = sum(1 for r in results if r.get('status') == 'passed')
total = len(results)
if total == 0:
    total = 1
resolved = 1 if passed == total else 0
print(f'PASSED={passed}')
print(f'RESOLVED={resolved}')
print(f'TOTAL={total}')
")"
else
    echo "    No eval results found at ${EVAL_JSON}"
    echo "    Searching for alternative result files..."

    ALT_EVAL=""
    for candidate in $(find "${RESULTS_DIR}" -name '*.eval.json' -o -name 'results*.json' 2>/dev/null | head -5); do
        if [ -f "${candidate}" ]; then
            echo "    Found: ${candidate}"
            ALT_EVAL="${candidate}"
            break
        fi
    done

    if [ -n "${ALT_EVAL}" ] && [ -f "${ALT_EVAL}" ]; then
        eval "$(python3 -c "
import json
with open('${ALT_EVAL}') as f:
    data = json.load(f)
resolved = 1 if data.get('score', 0) >= 1.0 else 0
total = 1
passed = resolved
print(f'PASSED={passed}')
print(f'RESOLVED={resolved}')
print(f'TOTAL={total}')
")"
    else
        echo "    No results files found. Marking as unresolved."
        PASSED=0
        RESOLVED=0
        TOTAL=1
    fi
fi

echo ""
echo "============================================"
if [ "${RESOLVED}" -gt 0 ]; then
    echo "  Result: RESOLVED (${PASSED}/${TOTAL} tests passed)"
else
    echo "  Result: NOT RESOLVED (${PASSED}/${TOTAL} tests passed)"
fi
echo "============================================"
echo ""

set -e

STATUS="success"

# cleanup trap will write the final result JSON and exit 0
