#!/usr/bin/env bash
set -euo pipefail

# benchmarks/run-harbor.sh — Unified Harbor runner for all benchmarks.
# Supports both single-task (--task) and full-dataset (--all) modes.
#
# Usage:
#   run-harbor.sh <benchmark> --task <id> [--timeout N] [--split S] [--preserve] [--solver S]
#   run-harbor.sh <benchmark> --all [--concurrency N] [--timeout N] [--split S] [--limit N] [--preserve] [--solver S]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
source "${SCRIPT_DIR}/config.sh"

# ── Argument parsing ──

if [ $# -lt 2 ]; then
    echo "Usage:"
    echo "  run-harbor.sh <benchmark> --task <id> [--timeout N] [--split S] [--preserve] [--solver S]"
    echo "  run-harbor.sh <benchmark> --all [--concurrency N] [--timeout N] [--split S] [--limit N] [--preserve] [--solver S]"
    exit 1
fi

BENCHMARK="$1"
shift

MODE=""
INSTANCE_ID=""
SOLVER_TIMEOUT="3600"
SPLIT=""
CONCURRENCY="5"
LIMIT_TASKS=""
PRESERVE_WORKSPACE="${PRESERVE_WORKSPACE:-}"
BENCHMARK_SOLVER="${BENCHMARK_SOLVER:-factory}"

while [ $# -gt 0 ]; do
    case "$1" in
        --task)        MODE="task"; INSTANCE_ID="$2"; shift 2 ;;
        --all)         MODE="all"; shift ;;
        --timeout)     SOLVER_TIMEOUT="$2"; shift 2 ;;
        --split)       SPLIT="$2"; shift 2 ;;
        --concurrency) CONCURRENCY="$2"; shift 2 ;;
        --limit)       LIMIT_TASKS="$2"; shift 2 ;;
        --preserve)    PRESERVE_WORKSPACE="1"; shift ;;
        --solver)      BENCHMARK_SOLVER="$2"; shift 2 ;;
        *)             echo "ERROR: Unknown option '$1'"; exit 1 ;;
    esac
done

if [ -z "${MODE}" ]; then
    echo "ERROR: Must specify --task <id> or --all"
    exit 1
fi

# ── Load benchmark configuration ──

benchmark_config "${BENCHMARK}"
benchmark_dataset "${BENCHMARK}" "${SPLIT}"

TASK_NAME=""
if [ "${MODE}" = "task" ]; then
    TASK_NAME="${INSTANCE_ID}"
    INSTANCE_ID=$(benchmark_instance_id "${BENCHMARK}" "${TASK_NAME}")
fi

if [ -n "${BENCH_LOCAL_PATH}" ]; then
    if [ "${MODE}" = "task" ]; then
        BENCH_LOCAL_PATH="${BENCH_LOCAL_PATH}/${TASK_NAME}"
    fi
    if [ ! -d "${BENCH_LOCAL_PATH}" ]; then
        echo "ERROR: Task directory not found: ${BENCH_LOCAL_PATH}"
        exit 1
    fi
fi

# ── Setup ──

HARBOR_DATASET="${BENCH_DATASET:-local}"

if [ "${MODE}" = "task" ]; then
    RUN_ID="ci-${BENCHMARK}-${TIMESTAMP}"
    RESULT_FILE="${CI_RESULTS_DIR}/${TIMESTAMP}-${BENCHMARK}-${BENCHMARK_SOLVER}.json"
    TOTAL=1
else
    RUN_ID="full-${BENCHMARK}-${TIMESTAMP}"
    RESULT_FILE="${CI_RESULTS_DIR}/${TIMESTAMP}-${BENCHMARK}-full.json"
    INSTANCE_ID="full-eval"
    TOTAL=0
fi

JOBS_DIR=""
RESULTS_DIR=""
PASSED=0
RESOLVED=0
PASS_RATE=0
COST_USD=0
INPUT_TOKENS=0
OUTPUT_TOKENS=0
CACHE_READ_TOKENS=0
CACHE_CREATION_TOKENS=0
TASKS_JSON="[]"

# ── Cleanup trap ──

cleanup() {
    local exit_code=$?

    LANGFUSE_TRACE_ID=""
    if [ "${MODE}" = "task" ] && [ -n "${JOBS_DIR}" ] && [ -d "${JOBS_DIR}" ]; then
        LANGFUSE_TRACE_ID=$(extract_trace_id "${JOBS_DIR}")
    fi

    if [ -n "${JOBS_DIR}" ] && [ -d "${JOBS_DIR}" ]; then
        if [ "${PRESERVE_WORKSPACE}" = "1" ]; then
            log "Preserving harbor jobs at ${JOBS_DIR} (--preserve)"
        else
            log "Cleaning up harbor jobs directory"
            rm -rf "${JOBS_DIR}"
        fi
    fi

    if [ -n "${RESULTS_DIR}" ] && [ -d "${RESULTS_DIR}" ]; then
        if [ "${PRESERVE_WORKSPACE}" = "1" ]; then
            log "Preserving results at ${RESULTS_DIR} (--preserve)"
        else
            log "Cleaning up results directory"
            rm -rf "${RESULTS_DIR}"
        fi
    fi

    if [ "${MODE}" = "task" ]; then
        PASSED="${RESOLVED}"
        if [ "${BENCHMARK}" = "featurebench" ]; then
            DETAILS_JSON='{"pass_rate": '"${PASS_RATE}"', "solver": "'"${BENCHMARK_SOLVER}"'", "cost_usd": '"${COST_USD}"', "input_tokens": '"${INPUT_TOKENS}"', "output_tokens": '"${OUTPUT_TOKENS}"', "cache_read_tokens": '"${CACHE_READ_TOKENS}"', "cache_creation_tokens": '"${CACHE_CREATION_TOKENS}"', "trace_id": "'"${LANGFUSE_TRACE_ID}"'"}'
        else
            DETAILS_JSON='{"solver": "'"${BENCHMARK_SOLVER}"'", "cost_usd": '"${COST_USD}"', "input_tokens": '"${INPUT_TOKENS}"', "output_tokens": '"${OUTPUT_TOKENS}"', "cache_read_tokens": '"${CACHE_READ_TOKENS}"', "cache_creation_tokens": '"${CACHE_CREATION_TOKENS}"', "trace_id": "'"${LANGFUSE_TRACE_ID}"'"}'
        fi
        write_result
    else
        local end_time duration
        end_time="$(date +%s)"
        duration=$(( end_time - START_TIME ))
        mkdir -p "${CI_RESULTS_DIR}"

        python3 -c "
import json, sys

tasks = json.loads('''${TASKS_JSON:-[]}''')
passed = sum(1 for t in tasks if t.get('resolved'))
total = len(tasks)
cost = sum(t.get('cost_usd', 0) for t in tasks)

result = {
    'benchmark': '${BENCHMARK}',
    'eval_type': 'full',
    'solver': '${BENCHMARK_SOLVER}',
    'passed': passed,
    'total': total,
    'score': round(passed / max(total, 1), 4),
    'duration_seconds': ${duration},
    'status': '${STATUS}',
    'timestamp': '${TIMESTAMP}',
    'details': {
        'cost_usd': round(cost, 4),
        'concurrency': ${CONCURRENCY},
        'dataset': '${HARBOR_DATASET}'
    },
    'tasks': tasks
}
json.dump(result, sys.stdout, indent=2)
print()
" > "${RESULT_FILE}" 2>/dev/null || true

        if [ -f "${RESULT_FILE}" ]; then
            echo ""
            log "Results written to ${RESULT_FILE}"
            cat "${RESULT_FILE}"
        fi
    fi

    if [ "${STATUS}" = "success" ]; then
        exit 0
    else
        exit "${exit_code:-1}"
    fi
}

trap cleanup EXIT

# ── Step 1: Display configuration ──

if [ "${MODE}" = "task" ]; then
    show_banner "${BENCHMARK}"
    log "Step 1: Configuration"
    echo "    Instance ID:     ${INSTANCE_ID}"
    echo "    Dataset:         ${BENCH_DATASET:-${BENCH_LOCAL_PATH}}"
    echo "    Solver timeout:  ${SOLVER_TIMEOUT}s ($(( SOLVER_TIMEOUT / 3600 ))h $(( (SOLVER_TIMEOUT % 3600) / 60 ))m)"
    echo "    Run ID:          ${RUN_ID}"
    echo "    Timestamp:       ${TIMESTAMP}"
else
    show_banner "Full Eval — ${BENCHMARK}"
    log "Step 1: Configuration"
    echo "    Benchmark:       ${BENCHMARK}"
    echo "    Dataset:         ${BENCH_DATASET:-${BENCH_LOCAL_PATH}}"
    echo "    Solver:          ${BENCHMARK_SOLVER}"
    echo "    Concurrency:     ${CONCURRENCY}"
    echo "    Solver timeout:  ${SOLVER_TIMEOUT}s ($(( SOLVER_TIMEOUT / 3600 ))h $(( (SOLVER_TIMEOUT % 3600) / 60 ))m)"
    echo "    Run ID:          ${RUN_ID}"
    echo "    Timestamp:       ${TIMESTAMP}"
fi
echo ""

# ── Step 2: Validate prerequisites ──

log "Step 2: Validating prerequisites"
validate_prerequisites

if [ -n "${BENCH_POST_EVAL_CMD}" ]; then
    echo "    programbench: checking availability via uvx..."
    if ! uvx programbench --help &>/dev/null 2>&1; then
        echo "    programbench: will be installed on first use via uvx"
    fi
    echo "    programbench: ready"
fi

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

# ── Step 3: Run Harbor evaluation ──

log "Step 3: Running Harbor evaluation"

if [ "${MODE}" = "task" ]; then
    JOBS_DIR="$(mktemp -d "/tmp/${BENCHMARK}-jobs-XXXXXX")"
else
    JOBS_DIR="$(mktemp -d "/tmp/full-eval-${BENCHMARK}-jobs-XXXXXX")"
fi
export JOBS_DIR
echo "    Jobs directory: ${JOBS_DIR}"
echo "    Started at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

TIMEOUT_MULTIPLIER=$(( SOLVER_TIMEOUT / 120 ))
[ "${TIMEOUT_MULTIPLIER}" -lt 1 ] && TIMEOUT_MULTIPLIER=1

MODEL="anthropic/claude-opus-4-6"

echo "    Model:           ${MODEL}"
echo "    Timeout mult:    ${TIMEOUT_MULTIPLIER}x"
if [ "${MODE}" = "task" ]; then
    echo "    Instance:        ${INSTANCE_ID}"
else
    echo "    Concurrency:     ${CONCURRENCY}"
fi
echo ""

cd "${HARNESS_DIR}"

# Build agent args
if [ "${BENCHMARK_SOLVER}" = "claude-code" ]; then
    AGENT_ARGS=(--agent claude-code)
    if [ -n "${BENCH_EXTRA_INSTRUCTION}" ]; then
        AGENT_ARGS+=(--extra-instruction-path "${HARNESS_DIR}/benchmarks/${BENCH_EXTRA_INSTRUCTION}")
    fi
    echo "    Agent:           claude-code (Harbor built-in)"
else
    AGENT_MODULE="${HARNESS_DIR}/benchmarks/factory_harbor_agent.py"
    export PYTHONPATH="$(dirname "${AGENT_MODULE}"):${PYTHONPATH:-}"
    AGENT_ARGS=(${BENCH_AGENT_IMPORT_FLAG} "${BENCH_AGENT_CLASS}")
    echo "    Agent:           factory (${BENCH_AGENT_CLASS#*:})"
fi

# Build Harbor command
HARBOR_CMD=(
    uvx harbor run
    --model "${MODEL}"
    --jobs-dir "${JOBS_DIR}"
    --agent-timeout-multiplier "${TIMEOUT_MULTIPLIER}"
)

if [ -n "${BENCH_LOCAL_PATH}" ]; then
    HARBOR_CMD+=(-p "${BENCH_LOCAL_PATH}")
else
    HARBOR_CMD+=(--dataset "${BENCH_DATASET}")
fi

HARBOR_CMD+=("${AGENT_ARGS[@]}")

if [ "${MODE}" = "task" ]; then
    case "${BENCH_FILTER_STYLE}" in
        glob)  HARBOR_CMD+=(--include-task-name "*${INSTANCE_ID}") ;;
        exact) HARBOR_CMD+=(--include-task-name "${INSTANCE_ID}") ;;
    esac
    HARBOR_CMD+=(--n-concurrent 1)
else
    HARBOR_CMD+=(--n-concurrent "${CONCURRENCY}")
    [ -n "${LIMIT_TASKS}" ] && HARBOR_CMD+=(--n-tasks "${LIMIT_TASKS}")
fi

# Allow-agent-host flags (programbench)
if [ -n "${BENCH_ALLOW_HOSTS}" ]; then
    for host in ${BENCH_ALLOW_HOSTS}; do
        HARBOR_CMD+=(--allow-agent-host "${host}")
    done
    if [ -n "${ANTHROPIC_VERTEX_PROJECT_ID:-}" ]; then
        for host in \
            us-east5-aiplatform.googleapis.com \
            us-central1-aiplatform.googleapis.com \
            europe-west1-aiplatform.googleapis.com \
            oauth2.googleapis.com \
            www.googleapis.com \
            storage.googleapis.com \
            metadata.google.internal; do
            HARBOR_CMD+=(--allow-agent-host "${host}")
        done
    fi
    LANGFUSE_HOSTNAME=$(extract_langfuse_hostname)
    if [ -n "${LANGFUSE_HOSTNAME}" ]; then
        HARBOR_CMD+=(--allow-agent-host "${LANGFUSE_HOSTNAME}")
    fi
fi

# Auth-specific --ae flags
AUTH_AE=()
if [ -n "${ANTHROPIC_VERTEX_PROJECT_ID:-}" ]; then
    GCLOUD_ADC="${GOOGLE_APPLICATION_CREDENTIALS:-${HOME}/.config/gcloud/application_default_credentials.json}"
    echo "    Auth mode:       Vertex AI (project: ${ANTHROPIC_VERTEX_PROJECT_ID})"
    AUTH_AE=(
        --ae "CLAUDE_CODE_USE_VERTEX=1"
        --ae "ANTHROPIC_VERTEX_PROJECT_ID=${ANTHROPIC_VERTEX_PROJECT_ID}"
        --ae "CLOUD_ML_REGION=${CLOUD_ML_REGION:-us-east5}"
        --ae "GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcloud-adc.json"
    )
else
    echo "    Auth mode:       Direct API (ANTHROPIC_API_KEY)"
fi

# Common --ae flags (written once)
COMMON_AE=(
    --ae "ANTHROPIC_MODEL=${ANTHROPIC_MODEL:-claude-opus-4-6[1m]}"
    --ae "CLAUDE_CODE_SUBAGENT_MODEL=${CLAUDE_CODE_SUBAGENT_MODEL:-claude-opus-4-6[1m]}"
    --ae "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=${CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS:-1}"
    --ae "ANTHROPIC_DEFAULT_OPUS_MODEL=${ANTHROPIC_DEFAULT_OPUS_MODEL:-claude-opus-4-6[1m]}"
    --ae "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=${CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING:-1}"
    --ae "MAX_THINKING_TOKENS=${MAX_THINKING_TOKENS:-128000}"
    --ae "CLAUDE_CODE_EFFORT_LEVEL=${CLAUDE_CODE_EFFORT_LEVEL:-XHIGH}"
    --ae "LANGFUSE_HOST=${LANGFUSE_HOST:-}"
    --ae "LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY:-}"
    --ae "LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET_KEY:-}"
    --ae "LANGFUSE_BASE_URL=${LANGFUSE_BASE_URL:-}"
    --ae "TELEMETRY_PLATFORM=${TELEMETRY_PLATFORM:-}"
    --ae "FACTORY_GIT_REF=${FACTORY_GIT_REF:-}"
    --ae "FACTORY_BENCHMARK=${BENCHMARK}"
    --ae "FACTORY_INSTANCE_ID=${INSTANCE_ID}"
)

HARBOR_CMD+=(${AUTH_AE[@]+"${AUTH_AE[@]}"} "${COMMON_AE[@]}")

if [ -n "${ANTHROPIC_VERTEX_PROJECT_ID:-}" ]; then
    HARBOR_CMD+=(--mounts '[{"type": "bind", "source": "'"${GCLOUD_ADC}"'", "target": "/tmp/gcloud-adc.json", "read_only": true}]')
fi

# Execute Harbor
HARBOR_EXIT=0
"${HARBOR_CMD[@]}" 2>&1 || HARBOR_EXIT=$?

if [ "${HARBOR_EXIT}" -ne 0 ]; then
    echo "    Harbor exited with code ${HARBOR_EXIT}"
fi

set +e

echo "    Finished at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# ── Step 4: Extract cost ──

log "Step 4: Extracting cost data"
extract_harbor_cost "${JOBS_DIR}"

# ── Step 5: Post-eval / Extract results ──

if [ -n "${BENCH_POST_EVAL_CMD}" ]; then
    # ProgramBench: run post-evaluation
    log "Step 5: Running post-evaluation"

    RESULTS_DIR="$(mktemp -d "/tmp/${BENCHMARK}-results-XXXXXX")"
    echo "    Results directory: ${RESULTS_DIR}"

    FOUND_SUBMISSIONS=0
    for submission in $(find "${JOBS_DIR}" -name 'submission.tar.gz' 2>/dev/null); do
        if [ ! -f "${submission}" ]; then continue; fi

        parent_dir=$(dirname "${submission}")
        parent_name=$(basename "${parent_dir}")
        if [ "${parent_name}" = "agent" ]; then
            trial_name=$(basename "$(dirname "${parent_dir}")")
        else
            trial_name="${parent_name}"
        fi
        SUBMISSION_INSTANCE_ID=$(echo "${trial_name}" | sed 's/__[A-Za-z0-9]\{7\}$//')
        if [ -z "${SUBMISSION_INSTANCE_ID}" ]; then
            SUBMISSION_INSTANCE_ID="${INSTANCE_ID}"
        fi

        EVAL_DIR="${RESULTS_DIR}/run/${SUBMISSION_INSTANCE_ID}"
        mkdir -p "${EVAL_DIR}"
        cp "${submission}" "${EVAL_DIR}/submission.tar.gz"
        FOUND_SUBMISSIONS=$((FOUND_SUBMISSIONS + 1))
        SUBMISSION_SIZE="$(du -h "${submission}" | cut -f1)"
        echo "    Found submission for ${SUBMISSION_INSTANCE_ID} (${SUBMISSION_SIZE})"
    done

    EVENTS_FILE=$(find "${JOBS_DIR}" -path '*/.factory/events.jsonl' -type f 2>/dev/null | head -1)
    if [ -n "${EVENTS_FILE}" ]; then
        cp "${EVENTS_FILE}" "${RESULTS_DIR}/events.jsonl"
        echo "    Extracted events.jsonl for debugging"
    fi

    if [ "${FOUND_SUBMISSIONS}" -gt 0 ]; then
        EVAL_EXIT=0
        ${BENCH_POST_EVAL_CMD} "${RESULTS_DIR}/run" -w 1 -b 4 --docker-cpus 4 --force \
            2>&1 || EVAL_EXIT=$?

        if [ "${EVAL_EXIT}" -ne 0 ]; then
            echo "    WARNING: Post-evaluation exited with code ${EVAL_EXIT}"
        fi
        echo "    Evaluation complete."

        if [ "${MODE}" = "task" ]; then
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
                ALT_EVAL=$(find "${RESULTS_DIR}" -name '*.eval.json' -o -name 'results*.json' 2>/dev/null | head -1)
                if [ -n "${ALT_EVAL}" ] && [ -f "${ALT_EVAL}" ]; then
                    echo "    Found: ${ALT_EVAL}"
                    eval "$(python3 -c "
import json
with open('${ALT_EVAL}') as f:
    data = json.load(f)
resolved = 1 if data.get('score', 0) >= 1.0 else 0
print(f'PASSED={resolved}')
print(f'RESOLVED={resolved}')
print(f'TOTAL=1')
")"
                else
                    echo "    No results files found. Marking as unresolved."
                    PASSED=0; RESOLVED=0; TOTAL=1
                fi
            fi
        else
            EVAL_PASSED=0
            EVAL_TOTAL=0
            for eval_json in $(find "${RESULTS_DIR}" -name '*.eval.json' 2>/dev/null); do
                EVAL_DATA=$(python3 -c "
import json
with open('${eval_json}') as f:
    data = json.load(f)
results = data.get('test_results', [])
passed = sum(1 for r in results if r.get('status') == 'passed')
print(f'{passed} {len(results)}')
" 2>/dev/null || echo "0 0")
                P=$(echo "${EVAL_DATA}" | cut -d' ' -f1)
                T=$(echo "${EVAL_DATA}" | cut -d' ' -f2)
                EVAL_PASSED=$((EVAL_PASSED + P))
                EVAL_TOTAL=$((EVAL_TOTAL + T))
            done
            if [ "${EVAL_TOTAL}" -gt 0 ]; then
                echo "    Post-eval: ${EVAL_PASSED}/${EVAL_TOTAL} tests passed"
            fi
        fi
    else
        echo "    WARNING: No submissions found in Harbor jobs directory"
        echo "    Contents of jobs directory:"
        find "${JOBS_DIR}" -type f 2>/dev/null | head -20 || echo "      (empty)"
    fi
    echo ""

elif [ "${MODE}" = "task" ]; then
    log "Step 5: Extracting results"
    extract_single_reward "${JOBS_DIR}"
fi

if [ "${MODE}" = "all" ]; then
    if [ -n "${BENCH_POST_EVAL_CMD}" ]; then
        log "Step 6: Extracting per-task results"
    else
        log "Step 5: Extracting per-task results"
    fi
    extract_multi_task_results
    PASSED=$(python3 -c "import json; tasks=json.loads('''${TASKS_JSON}'''); print(sum(1 for t in tasks if t.get('resolved')))")
    TOTAL=$(python3 -c "import json; tasks=json.loads('''${TASKS_JSON}'''); print(len(tasks))")
fi

# ── Display result summary ──

echo ""
echo "============================================"
if [ "${MODE}" = "task" ]; then
    if [ -n "${BENCH_POST_EVAL_CMD}" ]; then
        if [ "${RESOLVED}" -gt 0 ]; then
            echo "  Result: RESOLVED (${PASSED}/${TOTAL} tests passed)"
        else
            echo "  Result: NOT RESOLVED (${PASSED}/${TOTAL} tests passed)"
        fi
    else
        if [ "${RESOLVED}" -gt 0 ]; then
            echo "  Result: RESOLVED (${RESOLVED}/${TOTAL})"
        else
            echo "  Result: NOT RESOLVED (${RESOLVED}/${TOTAL})"
        fi
        if [ "${BENCHMARK}" = "featurebench" ]; then
            echo "  Pass Rate: ${PASS_RATE}"
        fi
    fi
else
    echo "  Full Eval Results: ${BENCHMARK}"
    echo "  Resolved: ${PASSED}/${TOTAL}"
    if [ "${TOTAL}" -gt 0 ]; then
        SCORE=$(python3 -c "print(round(${PASSED} / ${TOTAL} * 100, 1))")
        echo "  Accuracy: ${SCORE}%"
    fi
fi
echo "============================================"
echo ""

set -e

STATUS="success"
