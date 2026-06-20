#!/usr/bin/env bash
set -euo pipefail

# benchmarks/run-programbench.sh — Standalone CI pipeline for ProgramBench.
# Runs the complete solve+eval cycle: pull cleanroom image, start container,
# install Claude Code, run solver, package submission, evaluate with ProgramBench.

# ── Shared library ──

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# ── Configuration ──

TASK_NAME="${1:-cmatrix}"
SOLVER_TIMEOUT="${2:-3600}"

BENCHMARK="programbench"
RUN_ID="ci-programbench-${TIMESTAMP}"
RESULT_FILE="${CI_RESULTS_DIR}/${TIMESTAMP}-programbench.json"

# Task-specific mapping (hardcoded for cmatrix; extend as needed)
case "${TASK_NAME}" in
    cmatrix)
        INSTANCE_ID="abishekvashok__cmatrix.5c082c6"
        IMAGE="programbench/abishekvashok_1776_cmatrix.5c082c6:task_cleanroom"
        ;;
    *)
        echo "ERROR: Unknown ProgramBench task '${TASK_NAME}'"
        echo "Valid tasks: cmatrix"
        exit 1
        ;;
esac

CONTAINER_NAME="programbench-${TASK_NAME}-${TIMESTAMP}"
RESULTS_DIR=""

PASSED=0
RESOLVED=0
TOTAL=1

# ── Helpers ──

cleanup() {
    local exit_code=$?
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$" 2>/dev/null; then
        log "Copying factory events log for debugging"
        docker cp "${CONTAINER_NAME}:/workspace/.factory/events.jsonl" "${RESULTS_DIR}/events.jsonl" 2>/dev/null || true
        log "Stopping and removing container ${CONTAINER_NAME}"
        docker stop "${CONTAINER_NAME}" 2>/dev/null || true
        docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true
    fi
    if [ -n "${RESULTS_DIR}" ] && [ -d "${RESULTS_DIR}" ]; then
        if [ "${PRESERVE_WORKSPACE:-}" = "1" ]; then
            log "Preserving results at ${RESULTS_DIR} (PRESERVE_WORKSPACE=1)"
        else
            log "Cleaning up results directory"
            rm -rf "${RESULTS_DIR}"
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

show_banner "ProgramBench"
log "Step 1: Configuration"
echo "    Task name:       ${TASK_NAME}"
echo "    Instance ID:     ${INSTANCE_ID}"
echo "    Docker image:    ${IMAGE}"
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

echo "    programbench: checking availability via uvx..."
if ! uvx programbench --help &>/dev/null 2>&1; then
    echo "    programbench: will be installed on first use via uvx"
fi
echo "    programbench: ready"

check_gcloud_creds warning
setup_vertex_env

echo "    All prerequisites satisfied."
echo ""

# ── Step 3: Pull Docker image ──

log "Step 3: Pulling cleanroom image"
echo "    Image: ${IMAGE}"
docker pull "${IMAGE}"
echo "    Image pulled successfully."
echo ""

# ── Step 4: Start container ──

log "Step 4: Starting cleanroom container"
RESULTS_DIR="$(mktemp -d /tmp/programbench-results-XXXXXX)"
echo "    Results directory: ${RESULTS_DIR}"

GCLOUD_MOUNT_ARGS=()
GCLOUD_ADC="${GOOGLE_APPLICATION_CREDENTIALS:-${HOME}/.config/gcloud/application_default_credentials.json}"
if [ -f "${GCLOUD_ADC}" ]; then
    GCLOUD_MOUNT_ARGS=(-v "${GCLOUD_ADC}:/tmp/gcloud-adc.json:ro")
    echo "    Mounting gcloud credentials"
fi

docker run -d --name "${CONTAINER_NAME}" \
    -v "${RESULTS_DIR}:/results" \
    "${GCLOUD_MOUNT_ARGS[@]+"${GCLOUD_MOUNT_ARGS[@]}"}" \
    "${IMAGE}" \
    sleep infinity

echo "    Container ${CONTAINER_NAME} started."
echo ""

# ── Step 5: Install Claude Code inside container ──

log "Step 5: Installing Claude Code and Factory inside container"
echo "    Installing Node.js 22, Claude Code, and Factory..."

docker exec "${CONTAINER_NAME}" bash -c '
    apt-get update && apt-get install -y git rsync &&
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - &&
    apt-get install -y --no-install-recommends nodejs &&
    npm install -g @anthropic-ai/claude-code
'

docker exec "${CONTAINER_NAME}" bash -c '
    curl -LsSf https://astral.sh/uv/install.sh | sh &&
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH" &&
    uv tool install "remote-factory @ git+https://github.com/akashgit/remote-factory.git" &&
    which factory
'

echo "    Claude Code and Factory installed."

# Create non-root agent user (Claude Code refuses --dangerously-skip-permissions as root)
log "Step 5: Creating agent user"
docker exec "${CONTAINER_NAME}" bash -c '
    useradd -m -s /bin/bash agent 2>/dev/null || true
    chown -R agent:agent /workspace
    mkdir -p /home/agent/.claude /home/agent/.local /home/agent/.cargo
    cp -r /root/.claude/* /home/agent/.claude/ 2>/dev/null || true
    cp -r /root/.local/* /home/agent/.local/ 2>/dev/null || true
    cp -r /root/.cargo/* /home/agent/.cargo/ 2>/dev/null || true
    chown -R agent:agent /home/agent
'
echo "    Agent user created."
echo ""

# ── Step 5.1: Configure Claude Code ──

log "Step 5.1: Configuring Claude Code for headless use"

docker exec --user agent \
    -e CLAUDE_CODE_USE_VERTEX="${CLAUDE_CODE_USE_VERTEX:-}" \
    -e ANTHROPIC_VERTEX_PROJECT_ID="${ANTHROPIC_VERTEX_PROJECT_ID:-}" \
    -e CLOUD_ML_REGION="${CLOUD_ML_REGION:-}" \
    -e ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-opus-4-6[1m]}" \
    -e GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcloud-adc.json \
    "${CONTAINER_NAME}" bash -c '
    mkdir -p ~/.claude
    cat > ~/.claude/settings.json << SETTINGSEOF
{
  "permissions": {
    "allow": ["Bash(*)", "Read(*)", "Write(*)", "Edit(*)"],
    "deny": []
  },
  "env": {}
}
SETTINGSEOF

    # Smoke test — verify claude can authenticate
    export PATH="$HOME/.local/bin:$PATH"
    claude -p "say hello" --output-format json --max-turns 1 --permission-mode bypassPermissions 2>&1 | head -5
    echo "Claude Code smoke test exit: $?"
'

echo "    Claude Code configured."
echo ""

# ── Step 5.5: Prepare workspace for Factory ──

log "Step 5.5: Preparing workspace for Factory"

docker exec --user agent "${CONTAINER_NAME}" bash -c '
    cd /workspace &&
    git init &&
    git config user.email "solver@factory" &&
    git config user.name "Factory Solver" &&
    echo "executable" >> .gitignore &&
    git add -A &&
    git commit -m "initial cleanroom state" --allow-empty
'

docker exec --user agent "${CONTAINER_NAME}" bash -c 'cat > /workspace/factory.md << '\''FACTORYEOF'\''
---
goal: Reverse-engineer the compiled binary and produce equivalent source code
---
FACTORYEOF'

docker exec --user agent "${CONTAINER_NAME}" bash -c '
    mkdir -p ~/.claude/debug ~/.claude/projects ~/.claude/shell-snapshots ~/.claude/statsig ~/.claude/todos ~/.claude/skills
'

echo "    Workspace prepared for Factory."
echo ""

# ── Step 6: Run solver ──

log "Step 6: Running Factory CEO solver (timeout: ${SOLVER_TIMEOUT}s)"
echo "    Started at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

SOLVER_PROMPT='You are reverse-engineering a compiled binary at /workspace/executable.

The binary has EXECUTE-ONLY permissions (mode 111). You CANNOT read its contents. You can only run it.

Your goal: write source code and a compile.sh script that produces a behaviorally-equivalent executable at /workspace/executable.

Strategy:
1. Run the executable with various arguments to discover its behavior (--help, -h, no args, etc.)
2. Create test inputs and capture exact outputs
3. Read any documentation in /workspace/
4. Write source code matching the observed behavior
5. Create compile.sh that builds the executable
6. Test your implementation against the original using differential testing

Back up the original first: cp /workspace/executable /workspace/executable.bak
Your compile.sh must produce the executable at /workspace/executable.
The evaluation compares your output against the original on hidden test cases.'

SOLVER_PROMPT_FILE="$(mktemp /tmp/programbench-prompt-XXXXXX.txt)"
echo "${SOLVER_PROMPT}" > "${SOLVER_PROMPT_FILE}"
docker cp "${SOLVER_PROMPT_FILE}" "${CONTAINER_NAME}:/tmp/solver_prompt.txt"
docker exec "${CONTAINER_NAME}" chmod 644 /tmp/solver_prompt.txt
rm -f "${SOLVER_PROMPT_FILE}"

if [ -n "${ANTHROPIC_VERTEX_PROJECT_ID:-}" ]; then
    echo "    Using Vertex AI (project: ${ANTHROPIC_VERTEX_PROJECT_ID})"
fi

export_claude_env

set +e

SOLVER_EXIT=0
timeout "${SOLVER_TIMEOUT}" docker exec --user agent \
    -e CLAUDE_CODE_USE_VERTEX="${CLAUDE_CODE_USE_VERTEX:-}" \
    -e ANTHROPIC_VERTEX_PROJECT_ID="${ANTHROPIC_VERTEX_PROJECT_ID:-}" \
    -e CLOUD_ML_REGION="${CLOUD_ML_REGION:-}" \
    -e ANTHROPIC_MODEL="${ANTHROPIC_MODEL}" \
    -e CLAUDE_CODE_SUBAGENT_MODEL="${CLAUDE_CODE_SUBAGENT_MODEL}" \
    -e CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS="${CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS}" \
    -e ANTHROPIC_DEFAULT_OPUS_MODEL="${ANTHROPIC_DEFAULT_OPUS_MODEL}" \
    -e CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING="${CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING}" \
    -e MAX_THINKING_TOKENS="${MAX_THINKING_TOKENS}" \
    -e CLAUDE_CODE_EFFORT_LEVEL="${CLAUDE_CODE_EFFORT_LEVEL}" \
    -e DISABLE_AUTOUPDATER=1 \
    -e CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    -e CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 \
    -e GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcloud-adc.json \
    -e NODE_EXTRA_CA_CERTS= \
    -e SSL_CERT_FILE= \
    "${CONTAINER_NAME}" \
    bash -c 'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH" && cd /workspace && factory ceo . --headless --no-github --prompt /tmp/solver_prompt.txt' \
    2>&1 | tee /dev/stderr | tail -50 || true
SOLVER_EXIT=${PIPESTATUS[0]}

set -e

if [ "${SOLVER_EXIT}" -eq 124 ]; then
    echo "    Solver timed out after ${SOLVER_TIMEOUT}s"
elif [ "${SOLVER_EXIT}" -ne 0 ]; then
    echo "    Solver exited with code ${SOLVER_EXIT}"
fi

echo "    Finished at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# ── Step 6.5: Recover factory worktree changes ──

log "Step 6.5: Recovering factory worktree changes"

docker exec --user agent "${CONTAINER_NAME}" bash -c '
    set +e
    cd /workspace

    # Strategy 1: Merge surviving factory branch
    FACTORY_BRANCH=$(git branch --list "factory/*" | head -1 | tr -d " *")
    if [ -n "$FACTORY_BRANCH" ]; then
        echo "Merging factory branch: $FACTORY_BRANCH"
        git merge "$FACTORY_BRANCH" --no-edit 2>/dev/null || git cherry-pick "$FACTORY_BRANCH" --no-edit 2>/dev/null || true
    fi

    # Strategy 2: Recover orphaned commits via git fsck
    if [ -z "$FACTORY_BRANCH" ]; then
        echo "No factory branch, finding orphaned commits..."
        ORPHAN_COMMITS=$(git fsck --unreachable --no-reflogs 2>/dev/null | grep "unreachable commit" | awk "{print \$3}")
        if [ -n "$ORPHAN_COMMITS" ]; then
            # Pick the orphan with the most meaningful changes (most files changed, excluding factory artifacts)
            BEST_COMMIT=""
            BEST_CHANGES=0
            for SHA in $ORPHAN_COMMITS; do
                NUM_CHANGES=$(git diff-tree --no-commit-id --name-only -r "$SHA" 2>/dev/null | grep -v -E "^\.factory/|^eval/|^factory\.md" | wc -l)
                if [ "$NUM_CHANGES" -gt "$BEST_CHANGES" ]; then
                    BEST_CHANGES=$NUM_CHANGES
                    BEST_COMMIT=$SHA
                fi
            done
            if [ -n "$BEST_COMMIT" ] && [ "$BEST_CHANGES" -gt 0 ]; then
                echo "Recovering from orphan commit: $BEST_COMMIT ($BEST_CHANGES changed files)"
                echo "  Message: $(git log -1 --format="%s" $BEST_COMMIT 2>/dev/null)"
                git cherry-pick "$BEST_COMMIT" --no-edit 2>/dev/null \
                    || git checkout "$BEST_COMMIT" -- . 2>/dev/null \
                    || true
            else
                echo "No orphan commits with meaningful changes found"
            fi
        fi
    fi

    # Strategy 3: Recover from surviving worktree directories
    for wt in .factory/worktrees/*/; do
        if [ -d "$wt" ]; then
            echo "Recovering files from worktree: $wt"
            rsync -a --exclude=.git --exclude=.factory "$wt" ./ 2>/dev/null || true
        fi
    done

    exit 0
'

echo "    Worktree recovery complete."
echo ""

# ── Step 7: Package submission ──

log "Step 7: Packaging submission"

docker exec "${CONTAINER_NAME}" bash -c '
    cd /workspace
    if [ -f compile.sh ]; then bash compile.sh; fi
    mkdir -p /results
    tar -czf /results/submission.tar.gz \
        --exclude=.git --exclude=target \
        --exclude=executable.bak --exclude=./executable \
        --exclude=.factory --exclude=eval --exclude=factory.md .
'

docker cp "${CONTAINER_NAME}:/results/submission.tar.gz" "${RESULTS_DIR}/submission.tar.gz"

if [ -f "${RESULTS_DIR}/submission.tar.gz" ]; then
    SUBMISSION_SIZE="$(du -h "${RESULTS_DIR}/submission.tar.gz" | cut -f1)"
    echo "    Submission: ${RESULTS_DIR}/submission.tar.gz (${SUBMISSION_SIZE})"
else
    echo "    WARNING: No submission.tar.gz produced"
fi
echo ""

# ── Step 8: Run ProgramBench evaluation ──

log "Step 8: Running ProgramBench evaluation"

EVAL_DIR="${RESULTS_DIR}/run/${INSTANCE_ID}"
mkdir -p "${EVAL_DIR}"
cp "${RESULTS_DIR}/submission.tar.gz" "${EVAL_DIR}/submission.tar.gz"

EVAL_EXIT=0
uvx programbench eval "${RESULTS_DIR}/run" -w 1 -b 4 --docker-cpus 4 --force \
    2>&1 || EVAL_EXIT=$?

if [ "${EVAL_EXIT}" -ne 0 ]; then
    echo "    WARNING: ProgramBench evaluation exited with code ${EVAL_EXIT}"
fi

echo "    Evaluation complete."
echo ""

# ── Step 9: Extract and report results ──

log "Step 9: Extracting results"

EVAL_JSON="${EVAL_DIR}/${INSTANCE_ID}.eval.json"

if [ -f "${EVAL_JSON}" ]; then
    echo "    Eval file: ${EVAL_JSON}"
    eval "$(python3 -c "
import json

with open('${EVAL_JSON}') as f:
    data = json.load(f)

passed = 0
total = 0

if isinstance(data, dict):
    tests = data.get('tests', data.get('results', {}))
    if isinstance(tests, dict):
        for name, result in tests.items():
            total += 1
            if isinstance(result, dict) and result.get('passed', result.get('success', False)):
                passed += 1
            elif isinstance(result, bool) and result:
                passed += 1
    elif isinstance(tests, list):
        for result in tests:
            total += 1
            if isinstance(result, dict) and result.get('passed', result.get('success', False)):
                passed += 1
    elif 'score' in data:
        score = float(data['score'])
        total = 1
        passed = 1 if score > 0.5 else 0

if total == 0:
    total = 1

resolved = 1 if passed > 0 else 0
print(f'PASSED={passed}')
print(f'RESOLVED={resolved}')
print(f'TOTAL={total}')
")"
else
    echo "    No eval results found at ${EVAL_JSON}"
    echo "    Searching for alternative result files..."
    for candidate in $(find "${RESULTS_DIR}" -name '*.eval.json' -o -name 'results*.json' 2>/dev/null | head -5); do
        if [ -f "${candidate}" ]; then
            echo "    Found: ${candidate}"
            EVAL_JSON="${candidate}"
            break
        fi
    done

    if [ -n "${EVAL_JSON}" ] && [ -f "${EVAL_JSON}" ]; then
        eval "$(python3 -c "
import json
with open('${EVAL_JSON}') as f:
    data = json.load(f)
resolved = 1 if data.get('score', 0) > 0.5 else 0
total = 1
passed = resolved
print(f'PASSED={passed}')
print(f'RESOLVED={resolved}')
print(f'TOTAL={total}')
")"
    else
        echo "    No results files found. Marking as unresolved."
        PASSED=0
        RESOLVED=0
        TOTAL=1
    fi
fi

echo ""
echo "============================================"
if [ "${RESOLVED}" -gt 0 ]; then
    echo "  Result: RESOLVED (${PASSED}/${TOTAL} tests passed)"
else
    echo "  Result: NOT RESOLVED (${PASSED}/${TOTAL} tests passed)"
fi
echo "============================================"
echo ""

STATUS="success"

# cleanup trap will write the final result JSON and exit 0
