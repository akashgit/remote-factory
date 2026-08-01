#!/bin/bash
set -u

RESULTS_DIR="$1"
LOG="$RESULTS_DIR/master.log"
PARSER="$RESULTS_DIR/parse_tools.py"
FACTORY_ROOT="/Users/mathale/redhat-projects/remote-factory/.factory-worktrees/run-8b1db993"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

build_prompt() {
    local prompt_file="$RESULTS_DIR/ceo_prompt.md"
    local base="$FACTORY_ROOT/factory/agents/prompts/ceo.md"
    local playbook="$HOME/.factory/playbooks/ceo.md"
    cat "$base" > "$prompt_file"
    [ -f "$playbook" ] && { echo -e "\n\n# Behavioral Playbook\n"; cat "$playbook"; } >> "$prompt_file"
    echo "$prompt_file"
}

run_task() {
    local task_name="$1"
    local project_path="$2"
    local focus="$3"
    local iterations=5
    local timeout_s=120
    local task_dir="$RESULTS_DIR/$task_name"
    local prompt_file=$(build_prompt)

    mkdir -p "$task_dir"

    # Clean session summary before starting
    rm -f "$project_path/.factory/state/session_summary.md" 2>/dev/null

    local ceo_task="Project: $project_path
Mode: improve
Focus: $focus

You are the Factory CEO. Run an improve cycle on this project.
Start by reading the project state. Check .factory/state/session_summary.md first — if it exists, it contains a consolidated summary of the prior session state. Use it to understand where the prior session left off.
If no session summary exists, read the scattered .factory/ files (strategy/current.md, events.jsonl, reviews/, checkpoint.json, etc.) to reconstruct state.
Then proceed: observe, hypothesize, execute, finalize.
Read skills/workflow-improve/SKILL.md for the full improve playbook."

    log ""
    log "══════════════════════════════════════"
    log "TASK: $task_name"
    log "Project: $project_path"
    log "Focus: $focus"
    log "══════════════════════════════════════"

    for i in $(seq 1 $iterations); do
        log ""
        log "━━━ $task_name | Iteration $i/$iterations ━━━"

        # Check summary state BEFORE
        local summary_exists="no"
        local summary_bytes=0
        if [ -f "$project_path/.factory/state/session_summary.md" ]; then
            summary_exists="yes"
            summary_bytes=$(wc -c < "$project_path/.factory/state/session_summary.md" | tr -d ' ')
        fi
        log "  [BEFORE] session_summary.md: $summary_exists (${summary_bytes}B)"

        # FRESH session each time — new UUID, no --resume
        local session_id=$(cd  && uv run python3 -c "import uuid; print(uuid.uuid4())")
        local start_ts=$(date +%s)
        local iter_out="$task_dir/iter-${i}.jsonl"

        cd "$project_path"
        perl -e "alarm $timeout_s; exec @ARGV or die 'exec'" -- \
            claude -p "$ceo_task" \
            --session-id "$session_id" \
            --append-system-prompt-file "$prompt_file" \
            --verbose --output-format stream-json \
            --dangerously-skip-permissions \
            > "$iter_out" 2>&1
        local exit_code=$?
        cd - > /dev/null

        local end_ts=$(date +%s)
        local duration=$((end_ts - start_ts))

        # Parse tool calls
        local parse_out="$task_dir/iter-${i}-tools.txt"
        python3 "$PARSER" "$iter_out" > "$parse_out" 2>&1

        local total_tools=$(grep "TOTAL_TOOL_CALLS=" "$parse_out" | cut -d= -f2)
        local factory_reads=$(grep "FACTORY_READS=" "$parse_out" | cut -d= -f2)
        local tool_breakdown=$(grep "TOOL_BREAKDOWN=" "$parse_out" | cut -d= -f2-)

        # Extract which .factory/ files were read
        local factory_files=$(grep '\[\.factory/\]' "$parse_out" | sed 's/.*Read  *//' | sed 's/.*cat //' | sed 's/ \[\.factory.*$//' | tr '\n' ' | ' | sed 's/ | $//')

        log "  Exit: $exit_code | Duration: ${duration}s"
        log "  Total tool calls: $total_tools | .factory/ reads: $factory_reads"
        log "  Breakdown: $tool_breakdown"
        log "  .factory/ files read:"
        grep '\[\.factory/\]' "$parse_out" | while read line; do log "    $line"; done

        # CSV
        echo "$task_name,$i,$exit_code,$duration,$summary_exists,$summary_bytes,$total_tools,$factory_reads" \
            >> "$RESULTS_DIR/all_results.csv"

        # AFTER each iteration: generate session_summary.md to simulate interrupt handler
        log "  [AFTER] Generating session_summary.md..."
        cd /Users/mathale/redhat-projects/remote-factory/.factory-worktrees/run-8b1db993
        uv run python3 -c "
from pathlib import Path
from factory.statefulness import save_session_summary
save_session_summary(Path('$project_path'))
" 2>&1
        cd - > /dev/null

        if [ -f "$project_path/.factory/state/session_summary.md" ]; then
            local new_bytes=$(wc -c < "$project_path/.factory/state/session_summary.md" | tr -d ' ')
            log "  [AFTER] session_summary.md generated (${new_bytes}B)"
            cp "$project_path/.factory/state/session_summary.md" "$task_dir/summary-after-iter-${i}.md"
        else
            log "  [AFTER] session_summary.md NOT generated"
        fi
    done
}

echo "task,iter,exit,duration_s,summary_before,summary_bytes,total_tools,factory_reads" \
    > "$RESULTS_DIR/all_results.csv"

log "=== STATEFULNESS FRESH-SESSION EVAL ==="
log "Each iteration = FRESH claude session (new UUID, no --resume)"
log "Between iterations: generate session_summary.md"
log "Key question: does iter 2+ read session_summary.md instead of scattered files?"
log ""

# Task 1: factory-ui
run_task "factory-ui" \
    "/Users/mathale/factory-projects/factory-ui" \
    "dashboard rendering performance"

# Task 2: remote-factory — error handling
run_task "factory-errors" \
    "/Users/mathale/redhat-projects/remote-factory" \
    "agent timeout error handling"

# Task 3: remote-factory — eval reliability
run_task "factory-evals" \
    "/Users/mathale/redhat-projects/remote-factory" \
    "eval score reliability"

log ""
log "=== EVAL COMPLETE ==="
log ""
log "=== RESULTS ==="
column -t -s, "$RESULTS_DIR/all_results.csv"
log ""
log "=== ANALYSIS ==="
log "Iter 1: no summary → expect many .factory/ reads (baseline)"
log "Iter 2+: summary exists → expect fewer .factory/ reads if CEO uses it"
