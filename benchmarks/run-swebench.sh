#!/usr/bin/env bash
set -euo pipefail

# benchmarks/run-swebench.sh — Standalone CI pipeline for SWE-bench.
# Runs the complete solve+eval cycle: load instance, clone repo, run Claude Code solver,
# capture patch, evaluate with SWE-bench harness.

# ── Shared library ──

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# ── Configuration ──

INSTANCE_ID="${1:-sympy__sympy-20590}"
SOLVER_TIMEOUT="${2:-1800}"
DATASET="${3:-princeton-nlp/SWE-bench_Lite}"

BENCHMARK="swebench"
RUN_ID="ci-swebench-${TIMESTAMP}"
RESULT_FILE="${CI_RESULTS_DIR}/${TIMESTAMP}-swebench.json"

SWEBENCH="uvx --from swebench python"
WORKSPACE=""

PASSED=0
RESOLVED=0
TOTAL=1

# ── Helpers ──

cleanup() {
    local exit_code=$?
    if [ -n "${WORKSPACE}" ] && [ -d "${WORKSPACE}" ]; then
        if [ "${PRESERVE_WORKSPACE:-}" = "1" ]; then
            log "Preserving workspace at ${WORKSPACE} (PRESERVE_WORKSPACE=1)"
        else
            log "Cleaning up workspace"
            rm -rf "${WORKSPACE}"
        fi
    fi
    PASSED="${RESOLVED}"
    write_result
    if [ "${STATUS}" = "success" ]; then
        exit 0
    else
        exit "${exit_code:-1}"
    fi
}

trap cleanup EXIT

# ── Step 1: Parse and display configuration ──

show_banner "SWE-bench"
log "Step 1: Configuration"
echo "    Instance ID:     ${INSTANCE_ID}"
echo "    Dataset:         ${DATASET}"
echo "    Solver timeout:  ${SOLVER_TIMEOUT}s ($(( SOLVER_TIMEOUT / 3600 ))h $(( (SOLVER_TIMEOUT % 3600) / 60 ))m)"
echo "    Run ID:          ${RUN_ID}"
echo "    Timestamp:       ${TIMESTAMP}"
echo ""

# ── Step 2: Validate prerequisites ──

log "Step 2: Validating prerequisites"

MISSING=()

if ! command -v python3 &>/dev/null; then
    MISSING+=("python3 (install via your system package manager)")
fi

if ! command -v docker &>/dev/null && [ ! -x /usr/bin/docker ]; then
    MISSING+=("docker (install from https://docs.docker.com/get-docker/)")
fi

if ! command -v claude &>/dev/null; then
    MISSING+=("claude (Claude Code CLI — install from https://docs.anthropic.com/en/docs/claude-code)")
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "    ERROR: Missing prerequisites:"
    for m in "${MISSING[@]}"; do
        echo "      - ${m}"
    done
    exit 1
fi

echo "    python3: found"
echo "    docker: found"
echo "    claude: found"

ensure_uvx

# Verify swebench is usable via uvx
echo "    swebench: checking availability via uvx..."
if ! ${SWEBENCH} -c "import swebench; print(f'swebench {swebench.__version__}')" 2>/dev/null; then
    echo "    swebench: installing via uvx..."
    uvx --from swebench python -c "import swebench; print(f'swebench {swebench.__version__}')" || {
        echo "    ERROR: Failed to install/run swebench via uvx"
        exit 1
    }
fi
echo "    swebench: available"

check_gcloud_creds warning
setup_vertex_env

echo "    All prerequisites satisfied."
echo ""

# ── Step 3: Load instance from HuggingFace ──

log "Step 3: Loading instance ${INSTANCE_ID} from ${DATASET}"

INSTANCE_JSON="$(mktemp /tmp/swebench-instance-XXXXXX.json)"

${SWEBENCH} -c "
import json, sys
from datasets import load_dataset

ds = load_dataset('${DATASET}', split='test')
matches = [x for x in ds if x['instance_id'] == '${INSTANCE_ID}']
if not matches:
    print('ERROR: Instance ${INSTANCE_ID} not found in ${DATASET}', file=sys.stderr)
    sys.exit(1)
instance = matches[0]
json.dump({
    'instance_id': instance['instance_id'],
    'repo': instance['repo'],
    'base_commit': instance['base_commit'],
    'problem_statement': instance['problem_statement'],
}, open('${INSTANCE_JSON}', 'w'), indent=2)
print(f'Loaded: {instance[\"instance_id\"]}')
print(f'Repo:   {instance[\"repo\"]}')
print(f'Commit: {instance[\"base_commit\"][:12]}...')
"

if [ ! -s "${INSTANCE_JSON}" ]; then
    echo "    ERROR: Failed to load instance data"
    exit 1
fi

REPO="$(python3 -c "import json; print(json.load(open('${INSTANCE_JSON}'))['repo'])")"
BASE_COMMIT="$(python3 -c "import json; print(json.load(open('${INSTANCE_JSON}'))['base_commit'])")"

echo "    Instance loaded successfully."
echo ""

# ── Step 4: Setup workspace ──

log "Step 4: Setting up workspace"

WORKSPACE="$(mktemp -d /tmp/swebench-workspace-XXXXXX)"
echo "    Workspace: ${WORKSPACE}"
echo "    Cloning https://github.com/${REPO}..."

git clone --quiet "https://github.com/${REPO}.git" "${WORKSPACE}/repo"
cd "${WORKSPACE}/repo"
git checkout --quiet "${BASE_COMMIT}"

echo "    Checked out ${BASE_COMMIT:0:12}"
echo "    Working directory: ${WORKSPACE}/repo"

echo ""

# ── Step 5: Run solver (Claude Code) ──

log "Step 5: Running Claude Code solver (timeout: ${SOLVER_TIMEOUT}s)"
echo "    Started at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

SOLVER_PROMPT_FILE="${WORKSPACE}/solver_prompt.txt"
python3 -c "
import json
with open('${INSTANCE_JSON}') as f:
    instance = json.load(f)

# Rewrite /testbed/ paths to relative paths (we can't create /testbed symlink)
problem_statement = instance['problem_statement']
problem_statement = problem_statement.replace('/testbed/', './')

prompt = '''You are fixing a bug in an open-source Python project.

## Problem Statement

''' + problem_statement + '''

## Instructions

1. Read the problem statement carefully
2. Explore the repository to understand the codebase
3. Find the root cause of the bug
4. Implement a fix
5. Run the relevant tests to verify your fix works
6. Make sure you don't break existing tests

The repository is available at the current working directory.'''
with open('${SOLVER_PROMPT_FILE}', 'w') as f:
    f.write(prompt)
"

if [ -n "${ANTHROPIC_VERTEX_PROJECT_ID:-}" ]; then
    echo "    Using Vertex AI (project: ${ANTHROPIC_VERTEX_PROJECT_ID})"
fi

cd "${WORKSPACE}/repo"

export_claude_env

SOLVER_LOG="${WORKSPACE}/solver_output.log"
SOLVER_EXIT=0
timeout "${SOLVER_TIMEOUT}" claude -p "$(cat "${SOLVER_PROMPT_FILE}")" \
    --model "${ANTHROPIC_MODEL}" \
    --verbose \
    --max-turns 200 \
    --permission-mode bypassPermissions \
    --output-format stream-json \
    2>&1 | tee "${SOLVER_LOG}" | tail -50 || true
SOLVER_EXIT=${PIPESTATUS[0]}

if [ "${SOLVER_EXIT}" -eq 124 ]; then
    echo "    Solver timed out after ${SOLVER_TIMEOUT}s"
elif [ "${SOLVER_EXIT}" -ne 0 ]; then
    echo "    Solver exited with code ${SOLVER_EXIT}"
fi

echo "    Finished at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# ── Step 6: Capture patch ──

log "Step 6: Capturing patch"

cd "${WORKSPACE}/repo"
PATCH_FILE="${WORKSPACE}/model_patch.diff"
git diff > "${PATCH_FILE}"

PATCH_SIZE="$(wc -c < "${PATCH_FILE}")"
if [ "${PATCH_SIZE}" -eq 0 ]; then
    echo "    WARNING: Solver produced no changes (empty diff)"
    echo "    Evaluation will proceed but instance will not be resolved."
else
    PATCH_LINES="$(wc -l < "${PATCH_FILE}")"
    PATCH_FILES="$(git diff --name-only | wc -l)"
    echo "    Patch: ${PATCH_LINES} lines across ${PATCH_FILES} file(s)"
fi
echo ""

# ── Step 7: Write predictions.jsonl ──

log "Step 7: Writing predictions.jsonl"

PREDICTIONS_DIR="$(mktemp -d /tmp/swebench-predictions-XXXXXX)"
PREDICTIONS_FILE="${PREDICTIONS_DIR}/predictions.jsonl"

python3 -c "
import json

with open('${PATCH_FILE}') as f:
    model_patch = f.read()

prediction = {
    'instance_id': '${INSTANCE_ID}',
    'model_name_or_path': 'claude-code',
    'model_patch': model_patch,
}
with open('${PREDICTIONS_FILE}', 'w') as f:
    json.dump(prediction, f)
    f.write('\n')
print('    Written to ${PREDICTIONS_FILE}')
"

echo ""

# ── Step 8: Run SWE-bench evaluation ──

log "Step 8: Running SWE-bench evaluation"
echo "    This may take 10-30 minutes on first run (Docker image build)..."

cd "${HARNESS_DIR}"

EVAL_EXIT=0
${SWEBENCH} -m swebench.harness.run_evaluation \
    --dataset_name "${DATASET}" \
    --predictions_path "${PREDICTIONS_FILE}" \
    --instance_ids "${INSTANCE_ID}" \
    --max_workers 1 \
    --run_id "${RUN_ID}" \
    --cache_level env \
    --timeout 1800 \
    2>&1 || EVAL_EXIT=$?

if [ "${EVAL_EXIT}" -ne 0 ]; then
    echo "    ERROR: SWE-bench evaluation failed with exit code ${EVAL_EXIT}"
    exit 1
fi

echo "    Evaluation complete."
echo ""

# ── Step 9: Extract and report results ──

log "Step 9: Extracting results"

MODEL_NAME="claude-code"
RESULTS_JSON=""

for candidate in \
    "${HARNESS_DIR}/${MODEL_NAME}.${RUN_ID}.json" \
    "${HARNESS_DIR}/logs/run_evaluation/${RUN_ID}/${MODEL_NAME}/${INSTANCE_ID}/report.json" \
    "${HARNESS_DIR}/evaluation_results/${RUN_ID}/results.json"; do
    if [ -f "${candidate}" ]; then
        RESULTS_JSON="${candidate}"
        break
    fi
done

if [ -z "${RESULTS_JSON}" ]; then
    echo "    Known paths not found, searching for results..."
    for candidate in $(find "${HARNESS_DIR}" -maxdepth 3 -name "*${RUN_ID}*.json" 2>/dev/null | head -5); do
        if [ -f "${candidate}" ]; then
            RESULTS_JSON="${candidate}"
            break
        fi
    done
fi

if [ -n "${RESULTS_JSON}" ] && [ -f "${RESULTS_JSON}" ]; then
    echo "    Results file: ${RESULTS_JSON}"
    eval "$(python3 -c "
import json

with open('${RESULTS_JSON}') as f:
    data = json.load(f)

resolved = 0
total = 0

if isinstance(data, dict) and 'resolved_instances' in data:
    resolved = int(data['resolved_instances'])
    total = int(data.get('total_instances', 1))
elif isinstance(data, dict):
    for iid, result in data.items():
        if isinstance(result, dict):
            total += 1
            if result.get('resolved', False):
                resolved += 1
elif isinstance(data, list):
    for result in data:
        if isinstance(result, dict):
            total += 1
            if result.get('resolved', False):
                resolved += 1

print(f'RESOLVED={resolved}')
print(f'TOTAL={max(total, 1)}')
")"
else
    echo "    No results files found. Marking as unresolved."
    RESOLVED=0
    TOTAL=1
fi

echo ""
echo "============================================"
if [ "${RESOLVED}" -gt 0 ]; then
    echo "  Result: RESOLVED (${RESOLVED}/${TOTAL})"
else
    echo "  Result: NOT RESOLVED (${RESOLVED}/${TOTAL})"
fi
echo "============================================"
echo ""

STATUS="success"

# cleanup trap will write the final result JSON and exit 0
