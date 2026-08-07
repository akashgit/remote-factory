#!/usr/bin/env bash
set -euo pipefail

# benchmarks/prepull-swebench.sh — Pre-pull SWE-bench Docker images to avoid
# Docker Hub rate limits during concurrent Harbor runs.
#
# Usage:
#   prepull-swebench.sh <splits-dir> [--concurrency N]
#
# Reads instance IDs from train.jsonl + val.jsonl in <splits-dir>,
# converts them to Docker image names, and pulls them in parallel.
# Already-cached images are skipped (docker pull is a no-op for cached images).

SPLITS_DIR="${1:?Usage: prepull-swebench.sh <splits-dir> [--concurrency N]}"
CONCURRENCY="${2:-5}"

if [[ "${CONCURRENCY}" == "--concurrency" ]]; then
    CONCURRENCY="${3:-5}"
fi

if [ ! -d "${SPLITS_DIR}" ]; then
    echo "ERROR: Splits directory not found: ${SPLITS_DIR}"
    exit 1
fi

# Collect all instance IDs from split files
INSTANCE_IDS=()
for split_file in "${SPLITS_DIR}"/train.jsonl "${SPLITS_DIR}"/val.jsonl "${SPLITS_DIR}"/test.jsonl; do
    [ -f "${split_file}" ] || continue
    while IFS= read -r line; do
        [ -z "${line}" ] && continue
        iid=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['instance_id'])" "${line}" 2>/dev/null) || continue
        INSTANCE_IDS+=("${iid}")
    done < "${split_file}"
done

if [ ${#INSTANCE_IDS[@]} -eq 0 ]; then
    echo "No instance IDs found in ${SPLITS_DIR}"
    exit 0
fi

echo "==> Pre-pulling ${#INSTANCE_IDS[@]} SWE-bench Docker images (concurrency: ${CONCURRENCY})"

# Convert instance IDs to Docker image names and deduplicate
IMAGES=()
declare -A SEEN
for iid in "${INSTANCE_IDS[@]}"; do
    # repo__project-NNNNN -> swebench/sweb.eval.x86_64.repo_1776_project-NNNNN:latest
    img="swebench/sweb.eval.x86_64.${iid//__/_1776_}:latest"
    if [ -z "${SEEN[$img]+_}" ]; then
        SEEN[$img]=1
        IMAGES+=("${img}")
    fi
done

echo "    Unique images: ${#IMAGES[@]}"

# Check which images are already cached
TO_PULL=()
CACHED=0
for img in "${IMAGES[@]}"; do
    if docker image inspect "${img}" &>/dev/null; then
        CACHED=$((CACHED + 1))
    else
        TO_PULL+=("${img}")
    fi
done

echo "    Already cached: ${CACHED}"
echo "    Need to pull: ${#TO_PULL[@]}"

if [ ${#TO_PULL[@]} -eq 0 ]; then
    echo "==> All images already cached. Nothing to do."
    exit 0
fi

# Pull in parallel with controlled concurrency
RUNNING=0
PULLED=0
FAILED=0
PIDS=()
IMG_FOR_PID=()

for img in "${TO_PULL[@]}"; do
    docker pull "${img}" &>/dev/null &
    PIDS+=($!)
    IMG_FOR_PID+=("${img}")
    RUNNING=$((RUNNING + 1))

    if [ "${RUNNING}" -ge "${CONCURRENCY}" ]; then
        # Wait for any one to finish
        for i in "${!PIDS[@]}"; do
            if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
                wait "${PIDS[$i]}" 2>/dev/null && PULLED=$((PULLED + 1)) || FAILED=$((FAILED + 1))
                echo "    [$(( PULLED + FAILED ))/${#TO_PULL[@]}] ${IMG_FOR_PID[$i]}"
                unset 'PIDS[i]'
                unset 'IMG_FOR_PID[i]'
                RUNNING=$((RUNNING - 1))
                break
            fi
        done
        # Reindex arrays
        PIDS=("${PIDS[@]}")
        IMG_FOR_PID=("${IMG_FOR_PID[@]}")

        # If none finished yet, wait for the oldest
        if [ "${RUNNING}" -ge "${CONCURRENCY}" ]; then
            wait "${PIDS[0]}" 2>/dev/null && PULLED=$((PULLED + 1)) || FAILED=$((FAILED + 1))
            echo "    [$(( PULLED + FAILED ))/${#TO_PULL[@]}] ${IMG_FOR_PID[0]}"
            PIDS=("${PIDS[@]:1}")
            IMG_FOR_PID=("${IMG_FOR_PID[@]:1}")
            RUNNING=$((RUNNING - 1))
        fi
    fi
done

# Wait for remaining
for i in "${!PIDS[@]}"; do
    wait "${PIDS[$i]}" 2>/dev/null && PULLED=$((PULLED + 1)) || FAILED=$((FAILED + 1))
    echo "    [$(( PULLED + FAILED ))/${#TO_PULL[@]}] ${IMG_FOR_PID[$i]}"
done

echo ""
echo "==> Pre-pull complete: ${PULLED} pulled, ${FAILED} failed, ${CACHED} already cached"

if [ "${FAILED}" -gt 0 ]; then
    exit 1
fi
