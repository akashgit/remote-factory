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
import json, sys
_dj = '${DETAILS_JSON:-}'
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
