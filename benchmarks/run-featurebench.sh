#!/usr/bin/env bash
set -euo pipefail

# benchmarks/run-featurebench.sh — Standalone CI pipeline for FeatureBench.
# Runs the complete solve+eval cycle: load instance, clone repo, run Claude Code solver,
# capture patch, evaluate with FeatureBench harness.

# ── Shared library ──

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# ── Configuration ──

INSTANCE_ID="${1:-pypa__packaging.013f3b03.test_metadata.e00b5801.lv1}"
SOLVER_TIMEOUT="${2:-3600}"
SPLIT="${3:-fast}"

BENCHMARK="featurebench"
RUN_ID="ci-featurebench-${TIMESTAMP}"
RESULT_FILE="${CI_RESULTS_DIR}/${TIMESTAMP}-featurebench.json"

FB_CMD="uvx --from featurebench fb"
FB_PYTHON="uvx --from featurebench python"

DATASET="LiberCoders/FeatureBench"
WORKSPACE=""

PASSED=0
RESOLVED=0
PASS_RATE=0
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
    DETAILS_JSON='{"pass_rate": '"${PASS_RATE}"'}'
    write_result
    if [ "${STATUS}" = "success" ]; then
        exit 0
    else
        exit "${exit_code:-1}"
    fi
}

trap cleanup EXIT

# ── Step 1: Parse and display configuration ──

show_banner "FeatureBench"
log "Step 1: Configuration"
echo "    Instance ID:     ${INSTANCE_ID}"
echo "    Dataset:         ${DATASET}"
echo "    Split:           ${SPLIT}"
echo "    Solver timeout:  ${SOLVER_TIMEOUT}s ($(( SOLVER_TIMEOUT / 3600 ))h $(( (SOLVER_TIMEOUT % 3600) / 60 ))m)"
echo "    Run ID:          ${RUN_ID}"
echo "    Timestamp:       ${TIMESTAMP}"
echo ""

# ── Step 2: Validate prerequisites ──

log "Step 2: Validating prerequisites"

MISSING=()

if ! command -v python3 &>/dev/null; then
    MISSING+=("python3 >= 3.12 (install via your system package manager)")
fi

if ! command -v docker &>/dev/null && [ ! -x /usr/bin/docker ]; then
    MISSING+=("docker (install from https://docs.docker.com/get-docker/)")
fi

if ! command -v claude &>/dev/null; then
    MISSING+=("claude (Claude Code CLI — install from https://docs.anthropic.com/en/docs/claude-code)")
fi

if ! command -v factory &>/dev/null; then
    MISSING+=("factory (Factory CLI — install from the factory repo)")
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
echo "    factory: found"

ensure_uvx

# Verify featurebench is usable via uvx
echo "    featurebench: checking availability via uvx..."
if ! ${FB_CMD} --help &>/dev/null; then
    echo "    featurebench: installing via uvx..."
    ${FB_CMD} --help >/dev/null || {
        echo "    ERROR: Failed to install/run featurebench via uvx"
        exit 1
    }
fi
echo "    featurebench: available"

check_gcloud_creds warning
setup_vertex_env

echo "    All prerequisites satisfied."
echo ""

# ── Step 3: Load instance from HuggingFace ──

log "Step 3: Loading instance ${INSTANCE_ID} from ${DATASET}"

INSTANCE_JSON="$(mktemp /tmp/featurebench-instance-XXXXXX.json)"

${FB_PYTHON} -c "
import json, sys
from datasets import load_dataset

ds = load_dataset('${DATASET}', split='${SPLIT}')
matches = [x for x in ds if x['instance_id'] == '${INSTANCE_ID}']
if not matches:
    print('ERROR: Instance ${INSTANCE_ID} not found in ${DATASET} (split=${SPLIT})', file=sys.stderr)
    sys.exit(1)
instance = matches[0]
repo = instance['repo']
# Normalize repo format: __ -> / for GitHub clone URLs
if '/' not in repo and '__' in repo:
    repo = repo.replace('__', '/', 1)
json.dump({
    'instance_id': instance['instance_id'],
    'repo': repo,
    'base_commit': instance['base_commit'],
    'problem_statement': instance['problem_statement'],
}, open('${INSTANCE_JSON}', 'w'), indent=2)
print(f'Loaded: {instance[\"instance_id\"]}')
print(f'Repo:   {repo}')
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

WORKSPACE="$(mktemp -d /tmp/featurebench-workspace-XXXXXX)"
echo "    Workspace: ${WORKSPACE}"
echo "    Cloning https://github.com/${REPO}..."

git clone --quiet "https://github.com/${REPO}.git" "${WORKSPACE}/repo"
cd "${WORKSPACE}/repo"
git checkout --quiet "${BASE_COMMIT}"

echo "    Checked out ${BASE_COMMIT:0:12}"
echo "    Working directory: ${WORKSPACE}/repo"

echo ""

# ── Step 5: Run solver (Factory CEO) ──

log "Step 5: Running Factory CEO solver (timeout: ${SOLVER_TIMEOUT}s)"
echo "    Started at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

SOLVER_PROMPT_FILE="${WORKSPACE}/solver_prompt.txt"
python3 -c "
import json
with open('${INSTANCE_JSON}') as f:
    instance = json.load(f)

problem_statement = instance['problem_statement']

prompt = '''You are implementing a new feature in an open-source Python project.

## Problem Statement

''' + problem_statement + '''

## Instructions

1. Read the problem statement carefully — it describes a feature to implement
2. Explore the repository to understand the codebase architecture
3. Implement the feature as described in the problem statement
4. The problem statement contains detailed interface specifications — follow them exactly
5. Do NOT assume the feature is already implemented just because existing tests pass
6. The evaluation will use NEW tests (not the ones in the repo) to verify your implementation
7. Focus on implementing the interfaces, classes, and functions described in the problem statement
8. Make sure you don't break existing functionality

IMPORTANT: Even if existing tests pass, you MUST implement the feature described above.
The evaluation tests are DIFFERENT from the tests currently in the repository.

The repository is available at the current working directory.'''
with open('${SOLVER_PROMPT_FILE}', 'w') as f:
    f.write(prompt)
"

if [ -n "${ANTHROPIC_VERTEX_PROJECT_ID:-}" ]; then
    echo "    Using Vertex AI (project: ${ANTHROPIC_VERTEX_PROJECT_ID})"
fi

cd "${WORKSPACE}/repo"

# Create minimal factory.md so factory recognizes the project
cat > "${WORKSPACE}/repo/factory.md" << 'FACTORYEOF'
---
goal: Implement the feature described in the problem statement
---
FACTORYEOF

export_claude_env

# Temporarily allow failures — Steps 6-9 must always run regardless of solver/post-processing outcome
set +e

SOLVER_LOG="${WORKSPACE}/solver_output.log"
SOLVER_EXIT=0
timeout "${SOLVER_TIMEOUT}" factory ceo . \
    --headless \
    --no-github \
    --prompt "${SOLVER_PROMPT_FILE}" \
    2>&1 | tee "${SOLVER_LOG}" | tail -50 || true
SOLVER_EXIT=${PIPESTATUS[0]}

if [ "${SOLVER_EXIT}" -eq 124 ]; then
    echo "    Solver timed out after ${SOLVER_TIMEOUT}s"
elif [ "${SOLVER_EXIT}" -ne 0 ]; then
    echo "    Solver exited with code ${SOLVER_EXIT}"
fi

# Post-processing: merge factory branch changes back to default branch
FACTORY_BRANCH=$(cd "${WORKSPACE}/repo" && git branch --list 'factory/*' | head -1 | tr -d ' *')
if [ -n "${FACTORY_BRANCH}" ]; then
    echo "    Merging factory branch: ${FACTORY_BRANCH}"
    cd "${WORKSPACE}/repo" && git merge "${FACTORY_BRANCH}" --no-edit 2>/dev/null \
        || git cherry-pick "${FACTORY_BRANCH}" --no-edit 2>/dev/null || true
else
    echo "    No factory branch found, checking reflog..."
    LATEST=$(cd "${WORKSPACE}/repo" && git reflog --all --pretty=format:'%H %s' | grep -i 'factory\|cherry-pick\|fix' | head -1 | awk '{print $1}')
    if [ -n "${LATEST}" ]; then
        echo "    Cherry-picking from reflog: ${LATEST}"
        cd "${WORKSPACE}/repo" && git cherry-pick "${LATEST}" --no-edit 2>/dev/null || true
    fi
fi

# Recover from surviving worktrees
for wt in "${WORKSPACE}/repo/.factory/worktrees/"*/; do
    if [ -d "${wt}" ]; then
        echo "    Recovering files from worktree: ${wt}"
        rsync -a --exclude='.git' --exclude='.factory' "${wt}" "${WORKSPACE}/repo/" 2>/dev/null || true
    fi
done

set -e

echo "    Finished at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# ── Step 6: Capture patch ──

log "Step 6: Capturing patch"

cd "${WORKSPACE}/repo"
PATCH_FILE="${WORKSPACE}/model_patch.diff"
git diff "${BASE_COMMIT}" -- . ':!.factory' ':!eval' ':!factory.md' > "${PATCH_FILE}"

# Fallback: if committed diff is empty, try unstaged diff too
if [ ! -s "${PATCH_FILE}" ]; then
    echo "    No committed changes found, trying unstaged diff..."
    git diff -- . ':!.factory' ':!eval' ':!factory.md' > "${PATCH_FILE}"
fi

PATCH_SIZE="$(wc -c < "${PATCH_FILE}")"
if [ "${PATCH_SIZE}" -eq 0 ]; then
    echo "    WARNING: Solver produced no changes (empty diff)"
    echo "    Evaluation will proceed but instance will not be resolved."
else
    PATCH_LINES="$(wc -l < "${PATCH_FILE}")"
    PATCH_FILES="$(git diff "${BASE_COMMIT}" --name-only -- . ':!.factory' ':!eval' ':!factory.md' | wc -l)"
    echo "    Patch: ${PATCH_LINES} lines across ${PATCH_FILES} file(s)"
fi
echo ""

# ── Step 7: Write predictions.jsonl ──

log "Step 7: Writing predictions.jsonl"

PREDICTIONS_DIR="$(mktemp -d /tmp/featurebench-predictions-XXXXXX)"
PREDICTIONS_FILE="${PREDICTIONS_DIR}/predictions.jsonl"

python3 -c "
import json

with open('${PATCH_FILE}') as f:
    model_patch = f.read()

prediction = {
    'instance_id': '${INSTANCE_ID}',
    'model_name_or_path': 'claude-code',
    'model_patch': model_patch,
    'n_attempt': 1,
    'success': True,
}
with open('${PREDICTIONS_FILE}', 'w') as f:
    json.dump(prediction, f)
    f.write('\n')
print('    Written to ${PREDICTIONS_FILE}')
"

echo ""

# ── Step 8: Run FeatureBench evaluation ──

log "Step 8: Running FeatureBench evaluation"
echo "    This may take several minutes (Docker image pull + evaluation)..."

cd "${HARNESS_DIR}"

EVAL_EXIT=0
${FB_CMD} eval \
    -p "${PREDICTIONS_FILE}" \
    --split "${SPLIT}" \
    --task-id "${INSTANCE_ID}" \
    --n-concurrent 1 \
    2>&1 || EVAL_EXIT=$?

if [ "${EVAL_EXIT}" -ne 0 ]; then
    echo "    ERROR: FeatureBench evaluation failed with exit code ${EVAL_EXIT}"
    exit 1
fi

echo "    Evaluation complete."
echo ""

# ── Step 9: Extract and report results ──

log "Step 9: Extracting results"

RESULTS_JSON=""

for candidate in "${HARNESS_DIR}"/runs/*/eval_outputs/"${INSTANCE_ID}"/attempt-*/report.json; do
    if [ -f "${candidate}" ]; then
        RESULTS_JSON="${candidate}"
    fi
done

if [ -z "${RESULTS_JSON}" ]; then
    for candidate in "${HARNESS_DIR}"/runs/*/report.json; do
        if [ -f "${candidate}" ]; then
            RESULTS_JSON="${candidate}"
        fi
    done
fi

if [ -z "${RESULTS_JSON}" ]; then
    for candidate in "${HARNESS_DIR}"/*.json; do
        if [ -f "${candidate}" ] && python3 -c "
import json, sys
with open('${candidate}') as f:
    data = json.load(f)
if 'attempt_1' in data or '${INSTANCE_ID}' in data:
    sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
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
pass_rate = 0.0

# Per-instance report: {instance_id: {resolved: bool, pass_rate: float, ...}}
if '${INSTANCE_ID}' in data:
    result = data['${INSTANCE_ID}']
    total = 1
    if result.get('resolved', False):
        resolved = 1
    pass_rate = result.get('pass_rate', 0.0)

# Summary report: {attempt_1: {resolved_rate: float, pass_rate: float, ...}}
elif 'attempt_1' in data:
    attempt = data['attempt_1']
    total = attempt.get('total_instances', attempt.get('completed_instances', 1))
    resolved = attempt.get('resolved_instances', 0)
    pass_rate = attempt.get('pass_rate', 0.0)

# Flat dict with resolved/pass_rate keys
elif 'resolved' in data:
    total = 1
    if data.get('resolved', False):
        resolved = 1
    pass_rate = data.get('pass_rate', 0.0)

print(f'RESOLVED={resolved}')
print(f'TOTAL={max(total, 1)}')
print(f'PASS_RATE={pass_rate}')
")"
else
    echo "    No results files found. Marking as unresolved."
    RESOLVED=0
    TOTAL=1
    PASS_RATE=0
fi

echo ""
echo "============================================"
if [ "${RESOLVED}" -gt 0 ]; then
    echo "  Result: RESOLVED (${RESOLVED}/${TOTAL})"
else
    echo "  Result: NOT RESOLVED (${RESOLVED}/${TOTAL})"
fi
echo "  Pass Rate: ${PASS_RATE}"
echo "============================================"
echo ""

STATUS="success"

# cleanup trap will write the final result JSON and exit 0
