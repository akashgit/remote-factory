# Prototype Reference Data

Preserved outputs from the July 29, 2026 prototype statefulness evaluation runs.
These are historical reference data — NOT used by the new benchmark harness.

## Directories

### fresh-eval/

Source: `/tmp/statefulness-fresh-eval-1785331678/`

Fresh eval run using `--output-format stream-json --verbose`. Contains JSONL traces
for factory-ui (5 iterations) and factory-errors (3 iterations). This is the most
complete prototype dataset with parseable stream-JSON tool call data.

### build-mode-eval/

Source: `/tmp/claude-iter-eval-v2-1785326862/`

Build-mode iteration eval across todo-cli and weather-cli projects (5 iterations each).
JSONL traces only — generated project source code excluded to keep repository size small.

### agent-tracking-eval/

Source: `/tmp/factory-iter-eval-1785326611/`

Original iteration eval using plain stdout capture (not stream-JSON). Contains
human-readable log files for todo and weather projects (5 iterations each).
Limited parseability — no structured tool call data.

## Key Findings from Prototype

- All factory-ui iterations exited with code 142 (SIGALRM timeout)
- No `session_summary.md` was generated in any iteration
- The statefulness feature was not triggering during prototype runs
- The prototype used `perl -e 'alarm(120)'` for timeouts (not process-group safe)
- Missing `--verbose` flag in early runs meant tool calls were not captured

## What Changed in the New Harness

1. Uses `start_new_session=True` instead of `preexec_fn=os.setsid` for subprocess isolation
2. Always passes `--output-format stream-json --verbose` together
3. Stores results in `.factory/experiments/statefulness/` (not `/tmp/`)
4. Pytest-native with fixtures, parametrization, and early-exit on iteration 1 failure
5. Statistical analysis via Cohen's d + Bootstrap CI (not just raw counts)
