#!/usr/bin/env bash
set -euo pipefail

# benchmarks/run.sh — Unified entry point for single-task benchmark runs.
# Thin wrapper that dispatches to run-harbor.sh --task.
#
# Usage: benchmarks/run.sh <benchmark> <instance_id> [--timeout N] [--split S] [--preserve] [--solver S]
#
# Arguments:
<<<<<<< HEAD
#   benchmark      Required. One of: swebench, featurebench, terminalbench, programbench, legacybench, harborindex
=======
#   benchmark      Required. One of: swebench, featurebench, terminalbench, programbench, legacybench, tomswe
>>>>>>> b91b1e4 (feat: add ToM-SWE local Harbor task and update runner config)
#   instance_id    Required. Benchmark-specific instance identifier
#
# Options:
#   --timeout N    Solver timeout in seconds
#   --split S      Dataset split (featurebench only)
#   --preserve     Keep workspace/volumes after run
#   --solver S     Solver to use: factory (default) or claude-code

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Parse arguments ──

if [ $# -lt 2 ]; then
    echo "Usage: benchmarks/run.sh <benchmark> <instance_id> [--timeout N] [--split S] [--preserve] [--solver S]"
    echo ""
<<<<<<< HEAD
    echo "Benchmarks: swebench, featurebench, terminalbench, programbench, legacybench, harborindex"
=======
    echo "Benchmarks: swebench, featurebench, terminalbench, programbench, legacybench, tomswe"
>>>>>>> b91b1e4 (feat: add ToM-SWE local Harbor task and update runner config)
    exit 1
fi

BENCHMARK="$1"
INSTANCE_ID="$2"
shift 2

TIMEOUT=""
SPLIT=""
PRESERVE=""
SOLVER="factory"

while [ $# -gt 0 ]; do
    case "$1" in
        --timeout)  TIMEOUT="$2"; shift 2 ;;
        --split)    SPLIT="$2"; shift 2 ;;
        --preserve) PRESERVE=1; shift ;;
        --solver)   SOLVER="$2"; shift 2 ;;
        *)          echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Validate solver
case "${SOLVER}" in
    factory|claude-code) ;;
    *)
        echo "ERROR: Unknown solver '${SOLVER}'"
        echo "Valid solvers: factory, claude-code"
        exit 1
        ;;
esac

# Validate benchmark
case "${BENCHMARK}" in
    swebench|featurebench|terminalbench|programbench|legacybench|harborindex|tomswe) ;;
    *)
        echo "ERROR: Unknown benchmark '${BENCHMARK}'"
        echo "Valid benchmarks: swebench, featurebench, terminalbench, programbench, legacybench, harborindex, tomswe"
        exit 1
        ;;
esac

# ── Dispatch to unified runner ──

export BENCHMARK_SOLVER="${SOLVER}"

exec "${SCRIPT_DIR}/run-harbor.sh" "${BENCHMARK}" --task "${INSTANCE_ID}" \
    ${TIMEOUT:+--timeout "${TIMEOUT}"} \
    ${SPLIT:+--split "${SPLIT}"} \
    ${PRESERVE:+--preserve} \
    --solver "${SOLVER}"
