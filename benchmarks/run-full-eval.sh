#!/usr/bin/env bash
set -euo pipefail

# benchmarks/run-full-eval.sh — Run ALL tasks in a benchmark dataset through Harbor.
# Thin wrapper that dispatches to run-harbor.sh --all.
# For BENCHMARK=all, spawns parallel runs for each benchmark.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
source "${SCRIPT_DIR}/config.sh"

# ── Defaults ──

BENCHMARK=""
BENCHMARK_SOLVER="${BENCHMARK_SOLVER:-factory}"
CONCURRENCY="${CONCURRENCY:-5}"
SOLVER_TIMEOUT="${SOLVER_TIMEOUT:-3600}"
SPLIT=""
LIMIT_TASKS=""
PRESERVE_WORKSPACE="${PRESERVE_WORKSPACE:-}"

# ── Usage ──

usage() {
    echo "Usage: $(basename "$0") <benchmark> [options]"
    echo ""
    echo "Benchmarks: swebench, featurebench, terminalbench, programbench, legacybench, all"
    echo ""
    echo "Options:"
    echo "  --solver factory|claude-code   Solver to use (default: factory)"
    echo "  --concurrency N                Number of concurrent tasks (default: 5)"
    echo "  --timeout N                    Per-task solver timeout in seconds (default: 3600)"
    echo "  --split S                      Dataset split (featurebench only: full, lite)"
    echo "  --limit N                      Maximum number of tasks to run (optional)"
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
        --limit)       LIMIT_TASKS="$2"; shift 2 ;;
        --preserve)    PRESERVE_WORKSPACE="1"; shift ;;
        -h|--help)     usage ;;
        *)             echo "ERROR: Unknown option '$1'"; usage 1 ;;
    esac
done

# ── Handle 'all' — spawn parallel runs for each benchmark ──

if [ "${BENCHMARK}" = "all" ]; then
    log "Running full eval for ALL benchmarks (parallel)"
    echo ""
    FAILED=0
    FAILED_NAMES=()
    declare -A PIDS

    for BENCH in $(benchmark_all_names); do
        "${SCRIPT_DIR}/run-harbor.sh" "${BENCH}" --all \
            --solver "${BENCHMARK_SOLVER}" \
            --concurrency "${CONCURRENCY}" \
            --timeout "${SOLVER_TIMEOUT}" \
            ${LIMIT_TASKS:+--limit "${LIMIT_TASKS}"} \
            ${SPLIT:+--split "${SPLIT}"} \
            ${PRESERVE_WORKSPACE:+--preserve} &
        PIDS[${BENCH}]=$!
    done

    for BENCH in $(benchmark_all_names); do
        if ! wait "${PIDS[${BENCH}]}"; then
            log "FAILED: ${BENCH}"
            FAILED=$((FAILED + 1))
            FAILED_NAMES+=("${BENCH}")
        fi
    done

    echo ""
    if [ "${FAILED}" -gt 0 ]; then
        log "${FAILED} benchmark(s) failed: ${FAILED_NAMES[*]}"
        exit 1
    fi
    log "All benchmarks complete"
    exit 0
fi

# ── Single benchmark — dispatch to run-harbor.sh ──

exec "${SCRIPT_DIR}/run-harbor.sh" "${BENCHMARK}" --all \
    --solver "${BENCHMARK_SOLVER}" \
    --concurrency "${CONCURRENCY}" \
    --timeout "${SOLVER_TIMEOUT}" \
    ${SPLIT:+--split "${SPLIT}"} \
    ${LIMIT_TASKS:+--limit "${LIMIT_TASKS}"} \
    ${PRESERVE_WORKSPACE:+--preserve}
