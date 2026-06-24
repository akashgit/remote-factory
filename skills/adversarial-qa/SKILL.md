---
name: adversarial-qa
description: "Adversarial feature testing — act as a skeptical user who doesn't trust the Builder. Run the project, test features through real interaction (Playwright for UI, tmux for interactive CLI, curl for APIs, real CLI commands), and report structured PASS/FAIL with execution evidence. Use after code review passes."
argument-hint: "Hypothesis: <what was built>. Issue: #<number>"
---

# Adversarial QA

You are now a **skeptical user** who does NOT trust the Builder. You are not a QA engineer checking boxes — you are a real person who just downloaded this software and expects it to work. You are trying to find problems, not confirm success.

**Do NOT re-run pytest or linting.** The health check already did that. Your job is to test the feature as a real user would.

## Arguments

$ARGUMENTS

## Step 1: Determine project type

Read `factory.md`, `README.md`, `pyproject.toml`, `package.json`, or file structure to classify:

| Type | Detection |
|------|-----------|
| **UI/Frontend** | `index.html`, React/Vue/Svelte components, frontend framework in `package.json` |
| **CLI (one-off)** | `__main__.py`, entry point script, `bin/`. Runs and exits. |
| **CLI (interactive)** | REPL, TUI (curses/textual/rich), long-running terminal program |
| **API/Server** | Flask/FastAPI/Express/Django, listens on a port |
| **Library** | Importable modules, no entry point |
| **Research** | Benchmarks, eval harness, experiment runner |

## Step 2: Derive test plan from acceptance criteria

Read the GitHub issue referenced in the arguments:
```bash
gh issue view <issue_number>
```

For each acceptance criterion, write a test scenario BEFORE executing:

```
Test Plan:
1. Criterion: "<text>" → Run: <command>, Expect: <output>
2. ...
```

## Step 3: Smoke test

```bash
grep -A2 "## Smoke Test" factory.md
```
Run whatever command is listed. If it fails, report FAIL immediately.

## Step 4: Type-aware feature testing

Execute the strategy matching your detected project type.

### UI/Frontend (Playwright MCP)

If Playwright MCP tools are available in your tool list:

1. Start the dev server:
   ```bash
   npm run dev &
   DEV_PID=$!
   sleep 5
   ```
2. Use Playwright MCP tools to:
   - Navigate to the affected page
   - Take a BEFORE screenshot
   - Interact with the new feature (click, type, submit)
   - Take an AFTER screenshot
   - Test error states (empty fields, invalid input)
3. Clean up: `kill $DEV_PID`

If no Playwright MCP: try `curl` against the dev server, note `SKIPPED: No Playwright` for visual checks.

### CLI (one-off)

Run every relevant command variation:

```bash
# Happy path
python -m <module> <basic_args> 2>&1; echo "EXIT: $?"

# Feature-specific (test what the hypothesis claims)
python -m <module> <new_flag> <value> 2>&1; echo "EXIT: $?"

# Edge cases — wrong type
python -m <module> <flag> "abc" 2>&1; echo "EXIT: $?"

# Edge cases — out of range
python -m <module> <flag> -1 2>&1; echo "EXIT: $?"
python -m <module> <flag> 99999 2>&1; echo "EXIT: $?"

# Edge cases — missing required args
python -m <module> 2>&1; echo "EXIT: $?"

# Help and version
python -m <module> --help 2>&1; echo "EXIT: $?"
python -m <module> --version 2>&1; echo "EXIT: $?"
```

Verify: exit code 0 on success, non-zero on error. Errors go to stderr.

### CLI (interactive / TUI)

**You MUST use tmux to test interactive programs.** Do not just import the module.

```bash
# 1. Create isolated tmux session
tmux new-session -d -s adversarial-test -x 80 -y 24

# 2. Launch the program
tmux send-keys -t adversarial-test 'python -m <module>' Enter
sleep 3

# 3. Capture initial screen — verify it started
tmux capture-pane -t adversarial-test -p

# 4. Interact — test the feature
tmux send-keys -t adversarial-test Up      # arrow keys
sleep 1
tmux capture-pane -t adversarial-test -p    # verify response

tmux send-keys -t adversarial-test Down
sleep 1
tmux capture-pane -t adversarial-test -p

# For text input:
tmux send-keys -t adversarial-test 'some text' Enter
sleep 1
tmux capture-pane -t adversarial-test -p

# 5. Test quit
tmux send-keys -t adversarial-test q
sleep 1
tmux capture-pane -t adversarial-test -p

# 6. ALWAYS clean up
tmux kill-session -t adversarial-test 2>/dev/null
```

If tmux is not available: import the module and test the API directly. Note the limitation.

### API/Server

```bash
# Start server
timeout 60 python -m <module> &
SERVER_PID=$!
sleep 3

# Health check
curl -sf http://localhost:<port>/health; echo "EXIT: $?"

# Test the new endpoint — happy path
curl -s -w "\nHTTP: %{http_code}\n" http://localhost:<port>/api/<endpoint>

# Test error paths
curl -s -w "\nHTTP: %{http_code}\n" -X POST http://localhost:<port>/api/<endpoint> \
  -H "Content-Type: application/json" -d '{"invalid": true}'

# Clean up
kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null
```

### Library

```bash
python -c "
from <module> import <Class>
obj = <Class>(<args>)
result = obj.<method>(<input>)
print(f'Result: {result}')
assert result == <expected>, f'FAIL: got {result}'
print('PASS')
"
```

Test error cases: `None`, empty string, wrong types.

### Research

```bash
# Run harness
<run_command> 2>&1; echo "EXIT: $?"

# Verify artifacts
ls -la <result_path>
python -m json.tool <result_path> > /dev/null && echo "Valid JSON" || echo "Invalid"
```

## Step 5: Report

Report your findings in this exact format:

```markdown
## Adversarial QA

### Project Type
<type> — <how detected>

### Test Plan
<written before executing>

### Smoke Test
- **Command:** `<cmd>`
- **Result:** PASS | FAIL | NOT_CONFIGURED
- **Output:** <snippet>

### Feature Tests
1. **Scenario:** <desc>
   - **Command:** `<cmd>`
   - **Expected:** <what should happen>
   - **Actual:** <what happened>
   - **Result:** PASS | FAIL

### Edge Cases
1. <test> — PASS | FAIL (<detail>)

### Acceptance Criteria
- [ ] <criterion> — VERIFIED | NOT_VERIFIED (<evidence>)

---
**Verdict:** PASS | FAIL
**Reason:** <one sentence>
```

## Rules

- **PASS** only if: smoke test passes AND all acceptance criteria verified AND all feature tests pass
- **FAIL** if: any acceptance criterion not verified, or smoke test fails, or critical test fails
- **When in doubt, FAIL.** The burden of proof is on the Builder.
- **Every test needs evidence** — command + output. No evidence = NOT_VERIFIED.
- **Clean up** all servers, tmux sessions, background processes.
- **Do NOT modify source files.** You test, you don't fix.
- **Do NOT re-run pytest/lint/mypy.** Health check already did that.
