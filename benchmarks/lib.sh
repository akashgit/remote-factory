#!/usr/bin/env bash
# benchmarks/lib.sh — Shared functions for benchmark CI pipelines.
# Source this from individual benchmark scripts.

set -euo pipefail

# ── Shared State ──

HARNESS_DIR="${HARNESS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
TIMESTAMP="${TIMESTAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
CI_RESULTS_DIR="${CI_RESULTS_DIR:-${HARNESS_DIR}/benchmarks/results}"
START_TIME="${START_TIME:-$(date +%s)}"
STATUS="${STATUS:-failed}"

# ── Functions ──

log() { echo "==> $*"; }

show_banner() {
    local name="$1"
    echo "============================================"
    echo "  ${name} CI Pipeline"
    echo "============================================"
    echo ""
}

# write_result — Write a standardized result JSON file.
# Reads from environment variables set by the calling script:
#   BENCHMARK, INSTANCE_ID, PASSED, TOTAL, RESOLVED, STATUS, TIMESTAMP
#   RESULT_FILE — output path
#   DETAILS_JSON — optional JSON object string for benchmark-specific extras
write_result() {
    local end_time duration
    end_time="$(date +%s)"
    duration=$(( end_time - START_TIME ))
    mkdir -p "${CI_RESULTS_DIR}"
    python3 -c "
import json, os, sys
_dj = os.environ.get('DETAILS_JSON', '')
details = json.loads(_dj) if _dj else {}
result = {
    'benchmark': '${BENCHMARK}',
    'instance_id': '${INSTANCE_ID}',
    'solver': '${BENCHMARK_SOLVER:-unknown}',
    'passed': ${PASSED:-0},
    'total': ${TOTAL:-0},
    'score': round(${PASSED:-0} / max(${TOTAL:-0}, 1), 4),
    'resolved': bool(${RESOLVED:-0}),
    'duration_seconds': ${duration},
    'status': '${STATUS}',
    'timestamp': '${TIMESTAMP}',
    'details': details,
}
json.dump(result, sys.stdout, indent=2)
print()
" > "${RESULT_FILE}"
    echo ""
    log "Results written to ${RESULT_FILE}"
    cat "${RESULT_FILE}"
}

ensure_uvx() {
    if ! command -v uvx &>/dev/null; then
        if ! command -v uv &>/dev/null; then
            echo "    uv: not found, installing..."
            curl -LsSf https://astral.sh/uv/install.sh | sh
            export PATH="${HOME}/.local/bin:${PATH}"
            if ! command -v uv &>/dev/null; then
                echo "    ERROR: uv installation failed"
                exit 1
            fi
            echo "    uv: installed"
        fi
    fi
    echo "    uvx: ready"
}

# check_gcloud_creds — Check for application default credentials.
# Usage: check_gcloud_creds required   (exit 1 if missing)
#        check_gcloud_creds warning    (warn if missing)
check_gcloud_creds() {
    local mode="${1:-warning}"
    local creds_file="${GOOGLE_APPLICATION_CREDENTIALS:-${HOME}/.config/gcloud/application_default_credentials.json}"
    if [ ! -f "${creds_file}" ]; then
        if [ "${mode}" = "required" ]; then
            echo "    ERROR: gcloud application default credentials not found."
            echo "    Run: gcloud auth application-default login"
            exit 1
        else
            echo "    WARNING: gcloud application default credentials not found."
            echo "    If using Vertex AI, run: gcloud auth application-default login"
        fi
    else
        echo "    gcloud credentials: found"
    fi
}

# setup_vertex_env — Source .env if present, configure Vertex AI environment.
export_claude_env() {
    export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-opus-4-6[1m]}"
    export CLAUDE_CODE_SUBAGENT_MODEL="${CLAUDE_CODE_SUBAGENT_MODEL:-claude-opus-4-6[1m]}"
    export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS="${CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS:-1}"
    export ANTHROPIC_DEFAULT_OPUS_MODEL="${ANTHROPIC_DEFAULT_OPUS_MODEL:-claude-opus-4-6[1m]}"
    export CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING="${CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING:-1}"
    export MAX_THINKING_TOKENS="${MAX_THINKING_TOKENS:-128000}"
    export CLAUDE_CODE_EFFORT_LEVEL="${CLAUDE_CODE_EFFORT_LEVEL:-XHIGH}"
}

validate_prerequisites() {
    local MISSING=()
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
}

extract_langfuse_hostname() {
    local hostname=""
    if [ -n "${LANGFUSE_HOST:-}" ]; then
        hostname=$(echo "${LANGFUSE_HOST}" | sed 's|https\?://||' | sed 's|/.*||')
    elif [ -n "${LANGFUSE_BASE_URL:-}" ]; then
        hostname=$(echo "${LANGFUSE_BASE_URL}" | sed 's|https\?://||' | sed 's|/.*||')
    fi
    echo "${hostname}"
}

# create_langfuse_trace — Create a wrapper Langfuse trace via the REST API.
# Uses Python3 urllib (stdlib) so no pip install is needed.
# Echoes the 32-char hex trace ID on success, empty string on failure.
# Env: LANGFUSE_HOST (or LANGFUSE_BASE_URL), LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY
create_langfuse_trace() {
    local benchmark="${1:-}"
    local instance_id="${2:-}"
    local solver="${3:-}"
    local git_ref="${4:-}"

    python3 -c "
import json, os, sys, uuid, urllib.request, urllib.error, base64, time

host = os.environ.get('LANGFUSE_HOST') or os.environ.get('LANGFUSE_BASE_URL', '')
pub_key = os.environ.get('LANGFUSE_PUBLIC_KEY', '')
sec_key = os.environ.get('LANGFUSE_SECRET_KEY', '')

if not host or not pub_key or not sec_key:
    sys.exit(0)

host = host.rstrip('/')
trace_id = uuid.uuid4().hex
auth = base64.b64encode(f'{pub_key}:{sec_key}'.encode()).decode()

payload = {
    'batch': [{
        'id': uuid.uuid4().hex,
        'type': 'trace-create',
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime()),
        'body': {
            'id': trace_id,
            'name': 'benchmark:${benchmark}/${instance_id}',
            'metadata': {
                'benchmark': '${benchmark}',
                'instance_id': '${instance_id}',
                'solver': '${solver}',
                'git_ref': '${git_ref}',
                'source': 'run-harbor.sh',
            },
        },
    }],
    'metadata': {},
}

req = urllib.request.Request(
    f'{host}/api/public/ingestion',
    data=json.dumps(payload).encode(),
    headers={'Content-Type': 'application/json', 'Authorization': f'Basic {auth}'},
    method='POST',
)
try:
    urllib.request.urlopen(req, timeout=10)
except Exception:
    sys.exit(0)

print(trace_id)
" 2>/dev/null || true
}

# close_langfuse_trace — Mark a wrapper Langfuse trace as completed.
# Posts a span-end event with duration and status info.
close_langfuse_trace() {
    local trace_id="${1:-}"
    local status="${2:-unknown}"
    local duration="${3:-0}"

    if [ -z "${trace_id}" ]; then
        return 0
    fi

    python3 -c "
import json, os, sys, uuid, urllib.request, urllib.error, base64, time

host = os.environ.get('LANGFUSE_HOST') or os.environ.get('LANGFUSE_BASE_URL', '')
pub_key = os.environ.get('LANGFUSE_PUBLIC_KEY', '')
sec_key = os.environ.get('LANGFUSE_SECRET_KEY', '')

if not host or not pub_key or not sec_key:
    sys.exit(0)

host = host.rstrip('/')
auth = base64.b64encode(f'{pub_key}:{sec_key}'.encode()).decode()

payload = {
    'batch': [{
        'id': uuid.uuid4().hex,
        'type': 'span-create',
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime()),
        'body': {
            'id': uuid.uuid4().hex,
            'traceId': '${trace_id}',
            'name': 'harbor-execution',
            'startTime': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(time.time() - ${duration})),
            'endTime': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime()),
            'metadata': {
                'status': '${status}',
                'duration_seconds': ${duration},
            },
        },
    }],
    'metadata': {},
}

req = urllib.request.Request(
    f'{host}/api/public/ingestion',
    data=json.dumps(payload).encode(),
    headers={'Content-Type': 'application/json', 'Authorization': f'Basic {auth}'},
    method='POST',
)
try:
    urllib.request.urlopen(req, timeout=10)
except Exception:
    pass
" 2>/dev/null || true
}

extract_trace_id() {
    local jobs_dir="$1"
    local trace_id=""
    if [ -n "${jobs_dir}" ] && [ -d "${jobs_dir}" ]; then
        local trace_file
        trace_file=$(find "${jobs_dir}" -name 'trace_id.txt' -type f 2>/dev/null | head -1)
        if [ -n "${trace_file}" ] && [ -f "${trace_file}" ]; then
            trace_id=$(cat "${trace_file}" | tr -d '[:space:]')
        fi
    fi
    echo "${trace_id}"
}

# extract_harbor_cost — Extract cost/token data from Harbor jobs directory.
# Sets: COST_USD, INPUT_TOKENS, OUTPUT_TOKENS, CACHE_READ_TOKENS
# Reads: JOBS_DIR from environment
extract_harbor_cost() {
    local jobs_dir="${1:-${JOBS_DIR}}"
    COST_USD=0
    INPUT_TOKENS=0
    OUTPUT_TOKENS=0
    CACHE_READ_TOKENS=0
    CACHE_CREATION_TOKENS=0

    local harbor_result
    harbor_result=$(find "${jobs_dir}" -name 'result.json' -maxdepth 2 2>/dev/null | head -1)
    if [ -n "${harbor_result}" ]; then
        local cost_data
        cost_data=$(python3 -c "
import json
with open('${harbor_result}') as f:
    data = json.load(f)
stats = data.get('stats', {})
cost = stats.get('cost_usd', 0) or 0
input_t = stats.get('n_input_tokens', 0) or 0
output_t = stats.get('n_output_tokens', 0) or 0
cache_t = stats.get('n_cache_tokens', 0) or 0
if cost == 0:
    for trial in data.get('trials', {}).values():
        cost += trial.get('cost_usd', 0) or 0
print(f'COST_USD={cost}')
print(f'INPUT_TOKENS={input_t}')
print(f'OUTPUT_TOKENS={output_t}')
print(f'CACHE_READ_TOKENS={cache_t}')
" 2>/dev/null)
        eval "${cost_data}" 2>/dev/null || true
    fi

    if [ "${COST_USD}" = "0" ] || [ -z "${COST_USD}" ]; then
        local agent_log
        agent_log=$(find "${jobs_dir}" -name 'claude-code.txt' -o -name 'claude_code_stream_output.jsonl' -o -name 'factory-ceo.txt' 2>/dev/null | head -1)
        if [ -n "${agent_log}" ]; then
            local cost_data
            cost_data=$(grep 'total_cost_usd' "${agent_log}" 2>/dev/null | tail -1 | python3 -c "
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
            eval "${cost_data}" 2>/dev/null || true
        fi
    fi
}

# extract_single_reward — Extract reward from Harbor verifier output (single-task).
# Sets: RESOLVED, TOTAL, PASS_RATE
# Reads: JOBS_DIR from environment
extract_single_reward() {
    local jobs_dir="${1:-${JOBS_DIR}}"
    RESOLVED=0
    TOTAL=1
    PASS_RATE=0

    local reward_file=""
    for candidate in $(find "${jobs_dir}" -name 'reward.json' 2>/dev/null); do
        if [ -f "${candidate}" ]; then
            reward_file="${candidate}"
            break
        fi
    done

    if [ -z "${reward_file}" ]; then
        for candidate in $(find "${jobs_dir}" -name 'reward.txt' 2>/dev/null); do
            if [ -f "${candidate}" ]; then
                reward_file="${candidate}"
                break
            fi
        done
    fi

    if [ -n "${reward_file}" ] && [ -f "${reward_file}" ]; then
        echo "    Reward file: ${reward_file}"

        if [[ "${reward_file}" == *.json ]]; then
            eval "$(python3 -c "
import json
with open('${reward_file}') as f:
    data = json.load(f)
if isinstance(data, dict):
    values = [v for v in data.values() if isinstance(v, (int, float))]
    score = sum(values) / len(values) if values else 0.0
    resolved = 1 if score > 0.5 else 0
    pass_rate = score
elif isinstance(data, (int, float)):
    resolved = 1 if float(data) > 0.5 else 0
    pass_rate = float(data)
else:
    resolved = 0
    pass_rate = 0.0
print(f'RESOLVED={resolved}')
print(f'TOTAL=1')
print(f'PASS_RATE={pass_rate}')
")"
        else
            local reward_value
            reward_value="$(cat "${reward_file}" | tr -d '[:space:]')"
            echo "    Reward value: ${reward_value}"
            if [ "${reward_value}" = "1" ] || [ "${reward_value}" = "1.0" ]; then
                RESOLVED=1
                PASS_RATE=1.0
            else
                RESOLVED=0
                PASS_RATE="${reward_value}"
            fi
            TOTAL=1
        fi
    else
        local summary_file=""
        for candidate in $(find "${jobs_dir}" -name 'results*.json' -o -name 'summary*.json' 2>/dev/null); do
            if [ -f "${candidate}" ]; then
                summary_file="${candidate}"
                break
            fi
        done

        if [ -n "${summary_file}" ] && [ -f "${summary_file}" ]; then
            echo "    Summary file: ${summary_file}"
            eval "$(python3 -c "
import json
with open('${summary_file}') as f:
    data = json.load(f)
resolved = 0
total = 1
pass_rate = 0.0
if isinstance(data, dict):
    if 'reward' in data:
        resolved = 1 if float(data['reward']) > 0.5 else 0
        pass_rate = float(data['reward'])
    elif 'score' in data:
        resolved = 1 if float(data['score']) > 0.5 else 0
        pass_rate = float(data['score'])
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
        pass_rate = resolved / max(total, 1)
print(f'RESOLVED={resolved}')
print(f'TOTAL={max(total, 1)}')
print(f'PASS_RATE={pass_rate}')
")"
        else
            echo "    No results files found. Marking as unresolved."
            echo "    Contents of jobs directory:"
            find "${jobs_dir}" -type f 2>/dev/null | head -20 || echo "      (empty)"
            RESOLVED=0
            TOTAL=1
        fi
    fi
}

# extract_multi_task_results — Extract per-task results from Harbor jobs (full-eval).
# Sets: TASKS_JSON (JSON array string)
# Reads: JOBS_DIR from environment
extract_multi_task_results() {
    TASKS_JSON=$(python3 << 'PYEOF'
import json, os, re, sys, glob

jobs_dir = os.environ.get("JOBS_DIR", "")
if not jobs_dir or not os.path.isdir(jobs_dir):
    print("[]")
    sys.exit(0)

tasks = {}

def extract_instance_id(reward_path):
    """Extract instance_id from a reward file path.

    Harbor structure: $JOBS_DIR/<job>/<trial-name>/verifier/reward.{json,txt}
    Trial name format: <instance_id>__<7-char-suffix> e.g. matplotlib__matplotlib-14623__ff4rTkg
    """
    reward_dir = os.path.dirname(reward_path)
    if os.path.basename(reward_dir) == "verifier":
        trial_dir = os.path.basename(os.path.dirname(reward_dir))
    else:
        trial_dir = os.path.basename(reward_dir)
    return re.sub(r'__[A-Za-z0-9]{7}$', '', trial_dir)

for reward_path in sorted(glob.glob(os.path.join(jobs_dir, "**", "reward.json"), recursive=True)):
    instance_id = extract_instance_id(reward_path)
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
    instance_id = extract_instance_id(reward_path)
    if not instance_id or instance_id in tasks:
        continue

    try:
        with open(reward_path) as f:
            val = f.read().strip()
        resolved = val in ("1", "1.0")
    except Exception:
        resolved = False

    tasks[instance_id] = {"instance_id": instance_id, "resolved": resolved, "cost_usd": 0, "duration_seconds": 0}

# Extract per-trial cost/duration from per-trial result.json files
for rpath in sorted(glob.glob(os.path.join(jobs_dir, "**", "result.json"), recursive=True)):
    try:
        rdir = os.path.dirname(rpath)
        trial_id = extract_instance_id(rpath + "/dummy")
        if os.path.isdir(os.path.join(rdir, "verifier")) or os.path.isdir(os.path.join(rdir, "agent")):
            with open(rpath) as f:
                data = json.load(f)
            if trial_id in tasks:
                tasks[trial_id]["cost_usd"] = data.get("cost_usd", 0) or 0
                tasks[trial_id]["duration_seconds"] = data.get("duration_seconds", 0) or 0
    except Exception:
        pass

# Extract aggregate cost from job-level result.json and distribute evenly if no per-task costs
for rpath in sorted(glob.glob(os.path.join(jobs_dir, "*/result.json"))):
    try:
        with open(rpath) as f:
            data = json.load(f)
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
}

setup_vertex_env() {
    if [ -f "${HARNESS_DIR}/.env" ]; then
        echo "    .env: found at ${HARNESS_DIR}/.env"
        set -a
        source "${HARNESS_DIR}/.env"
        set +a
    elif [ -n "${ANTHROPIC_VERTEX_PROJECT_ID:-}" ]; then
        echo "    .env: not found, using environment variables"
    else
        echo "    WARNING: No .env file and ANTHROPIC_VERTEX_PROJECT_ID not set."
        echo "    Claude Code will use its default API configuration."
    fi

    if [ -n "${ANTHROPIC_VERTEX_PROJECT_ID:-}" ]; then
        export CLAUDE_CODE_USE_VERTEX=1
        export ANTHROPIC_VERTEX_PROJECT_ID
        export CLOUD_ML_REGION="${CLOUD_ML_REGION:-global}"
    fi
}
