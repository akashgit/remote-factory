#!/bin/bash
set -u

RESULTS_DIR="$1"
LOG="$RESULTS_DIR/master.log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

run_claude_iterations() {
    local task_name="$1"
    local prompt="$2"
    local task_dir="$RESULTS_DIR/$task_name"
    local project_dir="$task_dir/project"
    local session_id=$(python3 -c "import uuid; print(uuid.uuid4())")

    mkdir -p "$task_dir" "$project_dir"

    log ""
    log "====== TASK: $task_name ======"
    log "Prompt: $prompt"
    log "Session: $session_id"
    log ""

    for i in $(seq 1 5); do
        log "--- $task_name | Iteration $i/5 ---"
        local start_ts=$(date +%s)

        local iter_out="$task_dir/iter-${i}.jsonl"

        if [ $i -eq 1 ]; then
            # First iteration: new session
            cd "$project_dir"
            perl -e "alarm 120; exec @ARGV or die 'exec failed'" -- \
                claude -p "$prompt" \
                --verbose --output-format stream-json \
                --session-id "$session_id" \
                --dangerously-skip-permissions \
                > "$iter_out" 2>&1
            local exit_code=$?
            cd - > /dev/null
        else
            # Resume session
            cd "$project_dir"
            perl -e "alarm 120; exec @ARGV or die 'exec failed'" -- \
                claude --resume "$session_id" \
                -p "Continue building from where you left off. Check what's already done and proceed to the next step." \
                --verbose --output-format stream-json \
                --dangerously-skip-permissions \
                > "$iter_out" 2>&1
            local exit_code=$?
            cd - > /dev/null
        fi

        local end_ts=$(date +%s)
        local duration=$((end_ts - start_ts))

        # Parse tool calls from stream-json
        # Try multiple possible JSON key formats
        local tool_calls=""
        if [ -f "$iter_out" ]; then
            tool_calls=$(python3 << PYEOF
import json, sys
from collections import Counter

tools = Counter()
try:
    with open("$iter_out") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Check various stream-json formats
            if obj.get("type") == "content_block_start":
                cb = obj.get("content_block", {})
                if cb.get("type") == "tool_use":
                    tools[cb.get("name", "unknown")] += 1
            elif obj.get("type") == "tool_use":
                tools[obj.get("name", obj.get("tool_name", "unknown"))] += 1
            elif "tool_name" in obj:
                tools[obj["tool_name"]] += 1
except Exception as e:
    print(f"parse_error: {e}", file=sys.stderr)

if tools:
    print(", ".join(f"{name}={count}" for name, count in tools.most_common()))
else:
    print("none_detected")
PYEOF
)
        fi

        # Count source files in project
        local src_files=$(find "$project_dir" -type f \( -name "*.py" -o -name "*.js" -o -name "*.ts" -o -name "*.go" -o -name "*.rs" \) -not -path '*/.git/*' -not -path '*/.claude/*' -not -path '*/node_modules/*' 2>/dev/null | wc -l | tr -d ' ')

        # Count total lines of code
        local loc=0
        if [ "$src_files" -gt 0 ]; then
            loc=$(find "$project_dir" -type f \( -name "*.py" -o -name "*.js" -o -name "*.ts" \) -not -path '*/.git/*' -not -path '*/.claude/*' 2>/dev/null -exec cat {} + | wc -l | tr -d ' ')
        fi

        # Count total files
        local total_files=$(find "$project_dir" -type f -not -path '*/.git/*' -not -path '*/.claude/*' 2>/dev/null | wc -l | tr -d ' ')

        # Raw line count of stream output (proxy for activity)
        local output_lines=$(wc -l < "$iter_out" 2>/dev/null | tr -d ' ')

        log "  Exit: $exit_code | Duration: ${duration}s | Output lines: $output_lines"
        log "  Tool calls: $tool_calls"
        log "  Src files: $src_files | LOC: $loc | Total files: $total_files"

        echo "$task_name,$i,$exit_code,$duration,$output_lines,$src_files,$loc,$total_files,\"$tool_calls\"" \
            >> "$RESULTS_DIR/all_results.csv"

        log ""
    done

    # Final file listing
    log "--- $task_name | Final project state ---"
    find "$project_dir" -type f -not -path '*/.git/*' -not -path '*/.claude/*' 2>/dev/null | sort | head -30 | while read f; do
        log "  $f"
    done
    log ""
}

echo "task,iteration,exit_code,duration_s,output_lines,src_files,loc,total_files,tool_calls" \
    > "$RESULTS_DIR/all_results.csv"

log "=== CLAUDE CODE ITERATION EVAL START ==="

run_claude_iterations "weather-cli" \
    "Build a weather forecast CLI tool in Python that fetches current weather by city name using the OpenWeatherMap API. Use click for CLI, httpx for HTTP, and rich for terminal output. Include a pyproject.toml, tests, and a README."

run_claude_iterations "todo-cli" \
    "Build a todo list CLI tool in Python with SQLite storage. Support add, list, done, delete commands. Include priorities (high/medium/low), due dates, colored output with rich, and a pyproject.toml with tests."

run_claude_iterations "file-organizer" \
    "Build a file organizer CLI tool in Python that sorts files into folders by type (images, documents, audio, video, code). Support dry-run mode, undo via a log file, and recursive scanning. Use click and rich."

log "=== CLAUDE CODE ITERATION EVAL COMPLETE ==="

log ""
log "=== RESULTS TABLE ==="
column -t -s, "$RESULTS_DIR/all_results.csv"
