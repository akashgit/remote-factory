# Langfuse Trace Analysis

Scripts for pulling and analyzing factory CEO traces from Langfuse. Useful for understanding where time and tokens are spent during a factory cycle, identifying friction and wheel-spin, and comparing cycles.

## Setup

```bash
# From repo root
uv pip install requests python-dotenv matplotlib

# Create .env.local in repo root (or scripts/langfuse/) with your Langfuse credentials:
cat > .env.local << 'EOF'
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=pk-lf-dev-local-key
LANGFUSE_SECRET_KEY=sk-lf-dev-local-key
EOF
```

## Scripts

### `pull_langfuse_trace.py` — Fetch and inspect a trace

Pulls a trace from Langfuse and extracts three views:
1. **Agent orchestration timeline** — each agent span with duration, tool call count, input/output summaries
2. **CEO reasoning log** — the CEO's assistant messages showing its decision-making
3. **Factory CLI commands** — every `factory agent/begin/finalize/precheck/review` call the CEO made

```bash
cd scripts/langfuse

# Human-readable report
python pull_langfuse_trace.py <trace_id>

# Full text (no truncation)
python pull_langfuse_trace.py <trace_id> --full

# Machine-readable JSON
python pull_langfuse_trace.py <trace_id> --json > trace.json

# Save to file
python pull_langfuse_trace.py <trace_id> --full -o report.txt

# Force fresh fetch (skip cache)
python pull_langfuse_trace.py <trace_id> --no-cache
```

### `analyze_trace.py` — Token/time breakdown with charts

Computes per-agent token and wall-clock time distribution, then generates:
- **Pie charts** (token % and time % by agent role)
- **Swim-lane Gantt chart** (one row per role, bars for each agent invocation)
- **JSON breakdown** (for further analysis)

```bash
cd scripts/langfuse

# Print table + generate charts in current directory
python analyze_trace.py <trace_id>

# Output to a specific directory with a custom title
python analyze_trace.py <trace_id> -o ./output -t "My Project Cycle"

# Table only, no charts
python analyze_trace.py <trace_id> --no-charts

# Force fresh fetch
python analyze_trace.py <trace_id> --no-cache
```

Output files:
- `trace_breakdown.json` — raw per-agent data
- `trace_breakdown.png` — pie charts
- `trace_timeline.png` — Gantt chart

### `langfuse_client.py` — Shared module

Not run directly. Provides:
- `fetch_trace(trace_id)` — fetch with local file cache (`.trace_cache/`)
- `find_ancestor_agent(obs_id, obs_by_id)` — walk Langfuse parent chain to attribute an observation to its agent span
- `parse_ts()`, `truncate()`, `load_creds()` — utilities

## How it works

### Observation attribution

Langfuse records each tool call, assistant message, and thinking block as an **observation** parented to an agent **span** (e.g., `agent:builder`, `agent:qa`). The scripts walk the parent chain from each observation up to its nearest `agent:*` ancestor (excluding `agent:ceo`). Observations with no agent ancestor are attributed to the CEO.

### Token estimation

The current Langfuse setup does not record per-observation token counts (the `completionTokens` fields are all zero). Instead, output tokens are estimated as:

```
est_output_tokens = (assistant_message_chars + thinking_chars) / 4
```

This is a rough proxy (~4 chars per token for English text). It captures the model's output effort but does not account for input tokens (prompt, tool results).

### CEO duration

CEO wall-clock time is computed as: `total_cycle_time - sum(agent_span_durations)`. This represents the time the CEO spends between agent calls — reviewing outputs, writing verdicts, crafting task descriptions, running CLI commands.

### Caching

Traces are cached locally in `.trace_cache/<trace_id>.json` to avoid re-fetching 2MB+ payloads on every run. Use `--no-cache` to force a fresh fetch. The cache directory is gitignored.

## Finding trace IDs

- **Langfuse UI**: Navigate to your project's Traces tab. The trace ID is in the URL: `http://localhost:3000/project/<project>/traces?peek=<trace_id>`
- **Event log**: Factory writes trace IDs to `.factory/events.jsonl` on each cycle
- **By session**: Use the Langfuse API to list traces: `curl -u pk:sk http://localhost:3000/api/public/traces?limit=10`

## Example

The `snake_build_trace_analysis.md` file contains a complete analysis of trace `610f9acf79a46d79020e7eea614ba167` — an improve cycle on a terminal snake game project. See [issue #743](https://github.com/akashgit/remote-factory/issues/743) for the full friction analysis.

```bash
# Reproduce the analysis
cd scripts/langfuse
python analyze_trace.py 610f9acf79a46d79020e7eea614ba167 -o . -t "snake-test-v3 improve cycle"
python pull_langfuse_trace.py 610f9acf79a46d79020e7eea614ba167 --full -o snake_trace_report.txt
```
