#!/usr/bin/env bash
set -euo pipefail

# benchmarks/run-full-eval.sh — Run ALL tasks in a benchmark dataset through Harbor.
# Unlike the per-benchmark CI scripts (which run a single task via --include-task-name),
# this runs the full dataset with configurable concurrency and aggregates multi-task results.

# ── Shared library ──

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# ── Defaults ──

BENCHMARK=""
BENCHMARK_SOLVER="${BENCHMARK_SOLVER:-factory}"
CONCURRENCY="${CONCURRENCY:-5}"
SOLVER_TIMEOUT="${SOLVER_TIMEOUT:-3600}"
SPLIT=""
PRESERVE_WORKSPACE="${PRESERVE_WORKSPACE:-}"

# ── Usage ──

usage() {
    echo "Usage: $(basename "$0") <benchmark> [options]"
    echo ""
    echo "Benchmarks: swebench, featurebench, terminalbench, programbench"
    echo ""
    echo "Options:"
    echo "  --solver factory|claude-code   Solver to use (default: factory)"
    echo "  --concurrency N                Number of concurrent tasks (default: 5)"
    echo "  --timeout N                    Per-task solver timeout in seconds (default: 3600)"
    echo "  --split S                      Dataset split (featurebench only: full, lite)"
    echo "  --preserve                     Preserve Harbor jobs directory after completion"
    echo "  -h, --help                     Show this help message"
    exit "${1:-0}"
}

# ── Argument parsing ──

if [ $# -lt 1 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    usage
fi

BENCHMARK="$1"
shift

while [ $# -gt 0 ]; do
    case "$1" in
        --solver)      BENCHMARK_SOLVER="$2"; shift 2 ;;
        --concurrency) CONCURRENCY="$2"; shift 2 ;;
        --timeout)     SOLVER_TIMEOUT="$2"; shift 2 ;;
        --split)       SPLIT="$2"; shift 2 ;;
        --preserve)    PRESERVE_WORKSPACE="1"; shift ;;
        -h|--help)     usage ;;
        *)             echo "ERROR: Unknown option '$1'"; usage 1 ;;
    esac
done

# ── Map benchmark to Harbor dataset ──

HARBOR_DATASET=""
PROGRAMBENCH_LOCAL=""
EXTRA_HARBOR_ARGS=()

case "${BENCHMARK}" in
    swebench)
        HARBOR_DATASET="swe-bench/swe-bench-verified"
        ;;
    featurebench)
        case "${SPLIT:-full}" in
            lite)       HARBOR_DATASET="featurebench-lite" ;;
            full|fast)  HARBOR_DATASET="featurebench" ;;
            *)          HARBOR_DATASET="featurebench-${SPLIT}" ;;
        esac
        ;;
    terminalbench)
        HARBOR_DATASET="terminal-bench@2.0"
        ;;
    programbench)
        PROGRAMBENCH_LOCAL="${HARNESS_DIR}/benchmarks/programbench-harbor"
        if [ ! -d "${PROGRAMBENCH_LOCAL}" ]; then
            echo "ERROR: ProgramBench task directory not found: ${PROGRAMBENCH_LOCAL}"
            exit 1
        fi
        ;;
    *)
        echo "ERROR: Unknown benchmark '${BENCHMARK}'"
        echo "Valid benchmarks: swebench, featurebench, terminalbench, programbench"
        exit 1
        ;;
esac

# ── Configuration ──

RUN_ID="full-${BENCHMARK}-${TIMESTAMP}"
RESULT_FILE="${CI_RESULTS_DIR}/${TIMESTAMP}-${BENCHMARK}-full.json"
INSTANCE_ID="full-eval"

JOBS_DIR=""

PASSED=0
RESOLVED=0
TOTAL=0

# ── Helpers ──

cleanup() {
    local exit_code=$?
    if [ -n "${JOBS_DIR}" ] && [ -d "${JOBS_DIR}" ]; then
        if [ "${PRESERVE_WORKSPACE}" = "1" ]; then
            log "Preserving harbor jobs at ${JOBS_DIR} (--preserve)"
        else
            log "Cleaning up harbor jobs directory"
            rm -rf "${JOBS_DIR}"
        fi
    fi

    local end_time duration
    end_time="$(date +%s)"
    duration=$(( end_time - START_TIME ))
    mkdir -p "${CI_RESULTS_DIR}"

    python3 -c "
import json, sys

tasks = json.loads('${TASKS_JSON:-[]}')
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
    'duration_seconds': ${duration:-0},
    'status': '${STATUS}',
    'timestamp': '${TIMESTAMP}',
    'details': {
        'cost_usd': round(cost, 4),
        'concurrency': ${CONCURRENCY},
        'dataset': '${HARBOR_DATASET:-local}'
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

    if [ "${STATUS}" = "success" ]; then
        exit 0
    else
        exit "${exit_code:-1}"
    fi
}

trap cleanup EXIT

TASKS_JSON="[]"

# ── Step 1: Parse and display configuration ──

show_banner "Full Eval — ${BENCHMARK}"
log "Step 1: Configuration"
echo "    Benchmark:       ${BENCHMARK}"
echo "    Dataset:         ${HARBOR_DATASET:-${PROGRAMBENCH_LOCAL}}"
echo "    Solver:          ${BENCHMARK_SOLVER}"
echo "    Concurrency:     ${CONCURRENCY}"
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

log "Step 3: Running Harbor evaluation (full dataset)"

JOBS_DIR="$(mktemp -d /tmp/full-eval-${BENCHMARK}-jobs-XXXXXX)"
echo "    Jobs directory: ${JOBS_DIR}"
echo "    Started at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

TIMEOUT_MULTIPLIER=$(( SOLVER_TIMEOUT / 120 ))
[ "${TIMEOUT_MULTIPLIER}" -lt 1 ] && TIMEOUT_MULTIPLIER=1

MODEL="anthropic/claude-opus-4-6"

echo "    Model:           ${MODEL}"
echo "    Timeout mult:    ${TIMEOUT_MULTIPLIER}x"
echo "    Concurrency:     ${CONCURRENCY}"
echo ""

cd "${HARNESS_DIR}"

HARBOR_EXIT=0

if [ "${BENCHMARK_SOLVER}" = "claude-code" ]; then
    AGENT_ARGS=(--agent claude-code)
    if [ "${BENCHMARK}" = "featurebench" ]; then
        AGENT_ARGS+=(--extra-instruction-path "${HARNESS_DIR}/benchmarks/featurebench-extra-instructions.md")
    elif [ "${BENCHMARK}" = "terminalbench" ]; then
        AGENT_ARGS+=(--extra-instruction-path "${HARNESS_DIR}/benchmarks/terminalbench-extra-instructions.md")
    fi
    echo "    Agent:           claude-code (Harbor built-in)"
else
    AGENT_MODULE="${HARNESS_DIR}/benchmarks/factory_harbor_agent.py"
    export PYTHONPATH="$(dirname "${AGENT_MODULE}"):${PYTHONPATH:-}"
    if [ "${BENCHMARK}" = "programbench" ]; then
        AGENT_ARGS=(--agent factory_harbor_agent:ProgramBenchFactoryCeo)
    else
        AGENT_ARGS=(--agent-import-path factory_harbor_agent:FactoryCeo)
    fi
    echo "    Agent:           factory (FactoryCeo)"
fi

# Build dataset args — ProgramBench uses local path via -p
DATASET_ARGS=()
if [ -n "${PROGRAMBENCH_LOCAL}" ]; then
    DATASET_ARGS=(-p "${PROGRAMBENCH_LOCAL}")
else
    DATASET_ARGS=(--dataset "${HARBOR_DATASET}")
fi

# Build --allow-agent-host flags for ProgramBench (matching run-programbench.sh)
AGENT_ALLOW_HOSTS=()
if [ "${BENCHMARK}" = "programbench" ]; then
    if [ -n "${LANGFUSE_HOST:-}" ]; then
        LANGFUSE_HOSTNAME=$(echo "${LANGFUSE_HOST}" | sed 's|https\?://||' | sed 's|/.*||')
        AGENT_ALLOW_HOSTS+=(--allow-agent-host "${LANGFUSE_HOSTNAME}")
    elif [ -n "${LANGFUSE_BASE_URL:-}" ]; then
        LANGFUSE_HOSTNAME=$(echo "${LANGFUSE_BASE_URL}" | sed 's|https\?://||' | sed 's|/.*||')
        AGENT_ALLOW_HOSTS+=(--allow-agent-host "${LANGFUSE_HOSTNAME}")
    fi
fi

if [ -n "${ANTHROPIC_VERTEX_PROJECT_ID:-}" ]; then
    GCLOUD_ADC="${GOOGLE_APPLICATION_CREDENTIALS:-${HOME}/.config/gcloud/application_default_credentials.json}"
    echo "    Auth mode:       Vertex AI (project: ${ANTHROPIC_VERTEX_PROJECT_ID})"

    HARBOR_CMD=(
        uvx harbor run
        "${DATASET_ARGS[@]}"
        "${AGENT_ARGS[@]}"
        --model "${MODEL}"
        --n-concurrent "${CONCURRENCY}"
        --jobs-dir "${JOBS_DIR}"
        --agent-timeout-multiplier "${TIMEOUT_MULTIPLIER}"
    )

    if [ "${BENCHMARK}" = "programbench" ]; then
        HARBOR_CMD+=(
            --allow-agent-host api.anthropic.com
            --allow-agent-host us-east5-aiplatform.googleapis.com
            --allow-agent-host us-central1-aiplatform.googleapis.com
            --allow-agent-host europe-west1-aiplatform.googleapis.com
            --allow-agent-host oauth2.googleapis.com
            --allow-agent-host www.googleapis.com
            --allow-agent-host storage.googleapis.com
            --allow-agent-host metadata.google.internal
            --allow-agent-host sentry.io
            --allow-agent-host statsig.anthropic.com
            "${AGENT_ALLOW_HOSTS[@]}"
        )
    fi

    HARBOR_CMD+=(
        --ae "CLAUDE_CODE_USE_VERTEX=1"
        --ae "ANTHROPIC_VERTEX_PROJECT_ID=${ANTHROPIC_VERTEX_PROJECT_ID}"
        --ae "CLOUD_ML_REGION=${CLOUD_ML_REGION:-us-east5}"
        --ae "ANTHROPIC_MODEL=${ANTHROPIC_MODEL:-claude-opus-4-6[1m]}"
        --ae "GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcloud-adc.json"
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
        --mounts '[{"type": "bind", "source": "'"${GCLOUD_ADC}"'", "target": "/tmp/gcloud-adc.json", "read_only": true}]'
    )

    "${HARBOR_CMD[@]}" 2>&1 || HARBOR_EXIT=$?
else
    echo "    Auth mode:       Direct API (ANTHROPIC_API_KEY)"

    HARBOR_CMD=(
        uvx harbor run
        "${DATASET_ARGS[@]}"
        "${AGENT_ARGS[@]}"
        --model "${MODEL}"
        --n-concurrent "${CONCURRENCY}"
        --jobs-dir "${JOBS_DIR}"
        --agent-timeout-multiplier "${TIMEOUT_MULTIPLIER}"
    )

    if [ "${BENCHMARK}" = "programbench" ]; then
        HARBOR_CMD+=(
            --allow-agent-host api.anthropic.com
            --allow-agent-host sentry.io
            --allow-agent-host statsig.anthropic.com
            "${AGENT_ALLOW_HOSTS[@]}"
        )
    fi

    HARBOR_CMD+=(
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
    )

    "${HARBOR_CMD[@]}" 2>&1 || HARBOR_EXIT=$?
fi

if [ "${HARBOR_EXIT}" -ne 0 ]; then
    echo "    Harbor exited with code ${HARBOR_EXIT}"
fi

set +e

echo "    Finished at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# ── Step 4: Extract per-task results ──

log "Step 4: Extracting per-task results"

TASKS_JSON=$(python3 << 'PYEOF'
import json, os, sys, glob

jobs_dir = os.environ.get("JOBS_DIR", "")
if not jobs_dir or not os.path.isdir(jobs_dir):
    print("[]")
    sys.exit(0)

tasks = {}

for reward_path in sorted(glob.glob(os.path.join(jobs_dir, "**", "reward.json"), recursive=True)):
    parts = reward_path.split(os.sep)
    instance_id = None
    for i, p in enumerate(parts):
        if p == "trials" and i + 1 < len(parts):
            instance_id = parts[i + 1]
            break
    if not instance_id:
        continue

    try:
        with open(reward_path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            values = [v for v in data.values() if isinstance(v, (int, float))]
            score = sum(values) / len(values) if values else 0.0
            resolved = score > 0.5
        elif isinstance(data, (int, float)):
            resolved = float(data) > 0.5
        else:
            resolved = False
    except Exception:
        resolved = False

    if instance_id not in tasks:
        tasks[instance_id] = {"instance_id": instance_id, "resolved": resolved, "cost_usd": 0, "duration_seconds": 0}
    else:
        tasks[instance_id]["resolved"] = resolved

for reward_path in sorted(glob.glob(os.path.join(jobs_dir, "**", "reward.txt"), recursive=True)):
    parts = reward_path.split(os.sep)
    instance_id = None
    for i, p in enumerate(parts):
        if p == "trials" and i + 1 < len(parts):
            instance_id = parts[i + 1]
            break
    if not instance_id or instance_id in tasks:
        continue

    try:
        with open(reward_path) as f:
            val = f.read().strip()
        resolved = val in ("1", "1.0")
    except Exception:
        resolved = False

    tasks[instance_id] = {"instance_id": instance_id, "resolved": resolved, "cost_usd": 0, "duration_seconds": 0}

result_files = sorted(glob.glob(os.path.join(jobs_dir, "**", "result.json"), recursive=True))
for rpath in result_files:
    try:
        with open(rpath) as f:
            data = json.load(f)
        for trial_name, trial_data in data.get("trials", {}).items():
            if trial_name in tasks:
                tasks[trial_name]["cost_usd"] = trial_data.get("cost_usd", 0) or 0
                tasks[trial_name]["duration_seconds"] = trial_data.get("duration_seconds", 0) or 0
    except Exception:
        pass

    try:
        stats = data.get("stats", {})
        cost = stats.get("cost_usd", 0) or 0
        if cost > 0 and len(tasks) > 0:
            uncosted = [t for t in tasks.values() if t["cost_usd"] == 0]
            if len(uncosted) == len(tasks):
                per_task = cost / len(tasks)
                for t in tasks.values():
                    t["cost_usd"] = round(per_task, 4)
    except Exception:
        pass

task_list = sorted(tasks.values(), key=lambda t: t["instance_id"])
print(json.dumps(task_list))
PYEOF
)

PASSED=$(python3 -c "import json; tasks=json.loads('${TASKS_JSON}'); print(sum(1 for t in tasks if t.get('resolved')))")
TOTAL=$(python3 -c "import json; tasks=json.loads('${TASKS_JSON}'); print(len(tasks))")

echo ""
echo "============================================"
echo "  Full Eval Results: ${BENCHMARK}"
echo "  Resolved: ${PASSED}/${TOTAL}"
if [ "${TOTAL}" -gt 0 ]; then
    SCORE=$(python3 -c "print(round(${PASSED} / ${TOTAL} * 100, 1))")
    echo "  Accuracy: ${SCORE}%"
fi
echo "============================================"
echo ""

set -e

STATUS="success"
