#!/usr/bin/env bash
set -euo pipefail

# benchmarks/commit-full-eval.sh — Commit full eval results to the benchmark-data branch.
# Mirrors the CI workflow's commit pattern (benchmark.yml lines 220-293) but runs
# from any machine with git push access.

# ── Defaults ──

RESULTS_DIR="${RESULTS_DIR:-benchmarks/results}"
RUN_ID=""
REPO_URL=""
TEMP_DIR=""

# ── Usage ──

usage() {
    echo "Usage: $(basename "$0") [options]"
    echo ""
    echo "Options:"
    echo "  --results-dir DIR   Directory containing *-full.json files (default: benchmarks/results)"
    echo "  --run-id ID         Run identifier (default: auto-generated from timestamp)"
    echo "  -h, --help          Show this help message"
    exit 0
}

# ── Argument parsing ──

while [ $# -gt 0 ]; do
    case "$1" in
        --results-dir) RESULTS_DIR="$2"; shift 2 ;;
        --run-id)      RUN_ID="$2"; shift 2 ;;
        -h|--help)     usage ;;
        *)             echo "ERROR: Unknown option '$1'"; usage ;;
    esac
done

if [ -z "${RUN_ID}" ]; then
    RUN_ID="full-$(date -u +%Y%m%dT%H%M%SZ)"
fi

# ── Cleanup ──

cleanup() {
    if [ -n "${TEMP_DIR}" ] && [ -d "${TEMP_DIR}" ]; then
        rm -rf "${TEMP_DIR}"
    fi
}

trap cleanup EXIT

# ── Validate inputs ──

if [ ! -d "${RESULTS_DIR}" ]; then
    echo "ERROR: Results directory not found: ${RESULTS_DIR}"
    exit 1
fi

FULL_JSON_FILES=()
while IFS= read -r -d '' f; do
    FULL_JSON_FILES+=("$f")
done < <(find "${RESULTS_DIR}" -maxdepth 1 -name '*-full.json' -print0 2>/dev/null)

if [ ${#FULL_JSON_FILES[@]} -eq 0 ]; then
    echo "ERROR: No *-full.json files found in ${RESULTS_DIR}"
    exit 1
fi

echo "==> Found ${#FULL_JSON_FILES[@]} full eval result file(s)"
for f in "${FULL_JSON_FILES[@]}"; do
    echo "    $(basename "${f}")"
done

# ── Resolve repo URL ──

REPO_URL=$(git remote get-url origin 2>/dev/null || echo "")
if [ -z "${REPO_URL}" ]; then
    echo "ERROR: Could not determine git remote URL"
    exit 1
fi

# ── Get current commit info ──

CURRENT_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "")
CURRENT_REF=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
GIT_USER_NAME=$(git config user.name 2>/dev/null || echo "")
GIT_USER_EMAIL=$(git config user.email 2>/dev/null || echo "")

echo "==> Current state"
echo "    Commit: ${CURRENT_COMMIT:-unknown}"
echo "    Ref:    ${CURRENT_REF:-unknown}"
echo "    Run ID: ${RUN_ID}"
echo ""

# ── Clone benchmark-data branch ──

echo "==> Setting up benchmark-data branch"

TEMP_DIR="$(mktemp -d /tmp/benchmark-data-XXXXXX)"

if git ls-remote --exit-code --heads "${REPO_URL}" benchmark-data >/dev/null 2>&1; then
    echo "    Cloning existing benchmark-data branch..."
    git clone --single-branch --branch benchmark-data --depth 1 "${REPO_URL}" "${TEMP_DIR}/benchmark-data"
else
    echo "    Creating new benchmark-data branch..."
    mkdir -p "${TEMP_DIR}/benchmark-data"
    cd "${TEMP_DIR}/benchmark-data"
    git init
    git checkout -b benchmark-data
    git remote add origin "${REPO_URL}"
    touch results.jsonl full-eval-results.jsonl
    git add .
    if [ -n "${GIT_USER_NAME}" ]; then
        git config user.name "${GIT_USER_NAME}"
        git config user.email "${GIT_USER_EMAIL}"
    fi
    git commit -m "Initialize benchmark data branch"
    git push -u origin benchmark-data
fi

BENCHMARK_DATA_DIR="${TEMP_DIR}/benchmark-data"

# ── Enrich and append results ──

echo "==> Enriching and appending results"

python3 << PYEOF
import json, os, sys

results_dir = "${RESULTS_DIR}"
output = "${BENCHMARK_DATA_DIR}/full-eval-results.jsonl"
run_id = "${RUN_ID}"
commit = "${CURRENT_COMMIT}"
ref = "${CURRENT_REF}"

files = sorted([
    os.path.join(results_dir, f)
    for f in os.listdir(results_dir)
    if f.endswith("-full.json")
])

count = 0
for fpath in files:
    print(f"  Processing: {os.path.basename(fpath)}", file=sys.stderr)
    with open(fpath) as fh:
        data = json.load(fh)
    data["run_id"] = run_id
    data["commit"] = commit
    data["ref"] = ref
    data["trigger"] = "manual"
    with open(output, "a") as out:
        out.write(json.dumps(data) + "\n")
    count += 1

size = os.path.getsize(output) if os.path.exists(output) else 0
print(f"  Appended {count} result(s). File size: {size} bytes", file=sys.stderr)
PYEOF

echo ""

# ── Commit and push ──

echo "==> Committing results"

cd "${BENCHMARK_DATA_DIR}"

if [ -n "${GIT_USER_NAME}" ]; then
    git config user.name "${GIT_USER_NAME}"
    git config user.email "${GIT_USER_EMAIL}"
fi

git add full-eval-results.jsonl

if git diff --cached --quiet; then
    echo "    No changes to commit."
    exit 0
fi

git commit -m "benchmark: add full eval results from run ${RUN_ID} [skip ci]"

echo "==> Pushing to benchmark-data branch"
git push origin benchmark-data || (
    echo "    Push failed, retrying with rebase..."
    git pull --rebase origin benchmark-data && git push origin benchmark-data
)

echo ""
echo "==> Done. Full eval results committed to benchmark-data branch."
