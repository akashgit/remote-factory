#!/bin/bash
set -u

RESULTS_DIR="$1"
LOG="$RESULTS_DIR/master.log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

run_task_iterations() {
    local task_name="$1"
    local goal="$2"
    local dir_name="e2e-iter-$task_name"
    local project="$HOME/factory-projects/$dir_name"
    local iterations=5
    local timeout_s=120
    local task_dir="$RESULTS_DIR/$task_name"
    mkdir -p "$task_dir"

    log ""
    log "====== TASK: $task_name ======"
    log "Goal: $goal"
    log "Project: $project"
    log ""

    for i in $(seq 1 $iterations); do
        log "--- $task_name | Iteration $i/$iterations ---"

        # Count events BEFORE this iteration
        local events_before=0
        if [ -f "$project/.factory/events.jsonl" ]; then
            events_before=$(wc -l < "$project/.factory/events.jsonl" | tr -d ' ')
        fi

        # Run CEO with 2-min timeout (no --mode design, let it auto-detect → build mode)
        local start=$(date +%s)
        perl -e "alarm $timeout_s; exec @ARGV or die \"exec: \$!\"" -- \
            factory ceo "$goal" --dir "$dir_name" \
            > "$task_dir/iter-${i}-stdout.log" 2>&1
        local exit_code=$?
        local end=$(date +%s)
        local duration=$((end - start))

        # Count events AFTER
        local events_after=0
        if [ -f "$project/.factory/events.jsonl" ]; then
            events_after=$(wc -l < "$project/.factory/events.jsonl" | tr -d ' ')
        fi
        local new_events=$((events_after - events_before))

        # Extract agent calls from NEW events only
        local agents_started=""
        local agents_completed=""
        if [ "$new_events" -gt 0 ] && [ -f "$project/.factory/events.jsonl" ]; then
            agents_started=$(tail -n "$new_events" "$project/.factory/events.jsonl" | \
                grep '"agent.started"' | \
                python3 -c "import sys,json; [print(json.loads(l).get('agent','?')) for l in sys.stdin]" 2>/dev/null | \
                tr '\n' ',' | sed 's/,$//')
            agents_completed=$(tail -n "$new_events" "$project/.factory/events.jsonl" | \
                grep '"agent.completed"' | \
                python3 -c "import sys,json; [print(json.loads(l).get('agent','?')) for l in sys.stdin]" 2>/dev/null | \
                tr '\n' ',' | sed 's/,$//')
        fi

        # Check what files exist now
        local src_files=0
        [ -d "$project" ] && src_files=$(find "$project" -type f \( -name "*.py" -o -name "*.js" -o -name "*.ts" -o -name "*.go" \) -not -path '*/.git/*' -not -path '*/.factory/*' -not -path '*/.claude/*' 2>/dev/null | wc -l | tr -d ' ')

        local has_strategy="no"
        [ -f "$project/.factory/strategy/current.md" ] && has_strategy="yes"

        local has_config="no"
        [ -f "$project/.factory/config.json" ] && has_config="yes"

        # Check for PRs
        local pr_count=0
        if [ -d "$project/.git" ]; then
            pr_count=$(cd "$project" && gh pr list --state all 2>/dev/null | wc -l | tr -d ' ' || echo 0)
        fi

        # Log results
        log "  Exit: $exit_code | Duration: ${duration}s | New events: $new_events"
        log "  Agents started:   ${agents_started:-none}"
        log "  Agents completed: ${agents_completed:-none}"
        log "  Strategy: $has_strategy | Config: $has_config | Src files: $src_files | PRs: $pr_count"

        # Write to CSV
        echo "$task_name,$i,$exit_code,$duration,$new_events,\"$agents_started\",\"$agents_completed\",$has_strategy,$has_config,$src_files,$pr_count" \
            >> "$RESULTS_DIR/all_results.csv"

        log ""
    done
}

echo "task,iteration,exit_code,duration_s,new_events,agents_started,agents_completed,has_strategy,has_config,src_files,pr_count" \
    > "$RESULTS_DIR/all_results.csv"

log "=== ITERATION EVAL START ==="

# Task 1: Weather CLI
run_task_iterations "weather" \
    "Build a weather forecast CLI tool in Python that fetches current weather by city name using the OpenWeatherMap API"

# Task 2: Todo CLI
run_task_iterations "todo" \
    "Build a todo list CLI tool in Python with SQLite storage, priorities, due dates, and colored terminal output"

# Task 3: File organizer CLI
run_task_iterations "organizer" \
    "Build a file organizer CLI tool in Python that sorts files into folders by type, date, or size with a dry-run mode"

log "=== ITERATION EVAL COMPLETE ==="

# Print summary table
log ""
log "=== FULL RESULTS ==="
column -t -s, "$RESULTS_DIR/all_results.csv"
