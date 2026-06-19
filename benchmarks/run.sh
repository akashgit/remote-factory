#!/usr/bin/env bash
set -euo pipefail

# benchmarks/run.sh — Unified entry point for all benchmark runners.
#
# Usage: benchmarks/run.sh <benchmark> <instance_id> [--timeout N] [--split S] [--preserve]
#
# Arguments:
#   benchmark      Required. One of: swebench, featurebench, terminalbench, programbench
#   instance_id    Required. Benchmark-specific instance identifier
#
# Options:
#   --timeout N    Solver timeout in seconds
#   --split S      Dataset split (featurebench only)
#   --preserve     Keep workspace/volumes after run

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Parse arguments ──

if [ $# -lt 2 ]; then
    echo "Usage: benchmarks/run.sh <benchmark> <instance_id> [--timeout N] [--split S] [--preserve]"
    echo ""
    echo "Benchmarks: swebench, featurebench, terminalbench, programbench"
    exit 1
fi

BENCHMARK="$1"
INSTANCE_ID="$2"
shift 2

TIMEOUT=""
SPLIT=""
PRESERVE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --split)
            SPLIT="$2"
            shift 2
            ;;
        --preserve)
            PRESERVE=1
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ── Validate benchmark ──

case "${BENCHMARK}" in
    swebench|featurebench|terminalbench|programbench)
        ;;
    *)
        echo "ERROR: Unknown benchmark '${BENCHMARK}'"
        echo "Valid benchmarks: swebench, featurebench, terminalbench, programbench"
        exit 1
        ;;
esac

# ── Dispatch ──

case "${BENCHMARK}" in
    swebench)
        [ -n "${PRESERVE}" ] && export PRESERVE_WORKSPACE=1
        exec "${SCRIPT_DIR}/run-swebench.sh" "${INSTANCE_ID}" ${TIMEOUT:+"${TIMEOUT}"}
        ;;
    featurebench)
        [ -n "${PRESERVE}" ] && export PRESERVE_WORKSPACE=1
        # Split is $3, so if it's set we must also pass timeout as $2
        if [ -n "${SPLIT}" ]; then
            exec "${SCRIPT_DIR}/run-featurebench.sh" "${INSTANCE_ID}" "${TIMEOUT:-1800}" "${SPLIT}"
        else
            exec "${SCRIPT_DIR}/run-featurebench.sh" "${INSTANCE_ID}" ${TIMEOUT:+"${TIMEOUT}"}
        fi
        ;;
    terminalbench)
        [ -n "${PRESERVE}" ] && export PRESERVE_WORKSPACE=1
        exec "${SCRIPT_DIR}/run-terminalbench.sh" "${INSTANCE_ID}" ${TIMEOUT:+"${TIMEOUT}"}
        ;;
    programbench)
        [ -n "${PRESERVE}" ] && export PRESERVE_WORKSPACE=1
        exec "${SCRIPT_DIR}/run-programbench.sh" "${INSTANCE_ID}" ${TIMEOUT:+"${TIMEOUT}"}
        ;;
esac
