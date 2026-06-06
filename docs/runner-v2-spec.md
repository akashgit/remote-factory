# Runner Abstraction v2 — Technical Specification

## Architecture

```
CLI / invoke_agent()
        │
    AgentRunRequest (prompt, task, cwd, timeout, model, skip_permissions, role, session_name, project_path, extras)
        │
        ▼
    Runner Protocol
    ├── metadata() → RunnerMeta
    ├── build_command(request) → (cmd[], env{}, tmp_files[])
    ├── headless(request) → AgentRunResult
    └── interactive_run(request) → int
        │
        ├── ClaudeRunner    ── system prompt via --append-system-prompt-file
        ├── BobRunner       ── concatenated prompt
        ├── CodexRunner     ── concatenated prompt, CODEX_HOME isolation
        └── OpenCodeRunner  ── concatenated prompt, PATH auto-discovery
        │
        ▼
    run_subprocess() (shared executor)
        ── asyncio.create_subprocess_exec
        ── stdin=DEVNULL
        ── timeout + kill
        ── streaming via tee_stream
        ── returns AgentRunResult
```

## Data Models

### AgentRunRequest (factory/models.py)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| prompt | str | — | Agent role system prompt |
| task | str | — | The specific task to execute |
| cwd | Path | — | Working directory |
| timeout | float | 600.0 | Max seconds before kill |
| model | str \| None | None | Model override |
| skip_permissions | bool | True | Auto-approve all actions |
| role | str | "unknown" | Agent role name for logging |
| session_name | str \| None | None | Session identifier |
| project_path | Path \| None | None | Project root for .factory/ access |
| extras | dict[str, object] | {} | Runner-specific config (tmux_persist, etc.) |

### AgentRunResult (factory/models.py)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| stdout | str | — | Captured output |
| return_code | int | — | Process exit code |
| usage | AgentUsage \| None | None | Token telemetry (Claude only) |
| metadata | dict[str, object] | {} | stderr, runner-specific data |

### RunnerMeta (factory/runners/protocol.py)

| Field | Type | Default |
|-------|------|---------|
| name | str | — |
| display_name | str | — |
| binary | str | — |
| install_hint | str | — |
| required_env_vars | list[str] | [] |
| supports_model_override | bool | True |
| supports_interactive | bool | True |
| supports_streaming | bool | True |
| supports_usage_telemetry | bool | False |
| supports_session_name | bool | False |

Methods: `is_available() → bool` (shutil.which), `check_auth() → bool` (env vars check)

## Plugin Discovery

Third-party runners register via Python entry points:

```toml
# In third-party package pyproject.toml:
[project.entry-points."factory.runners"]
myrunner = "my_package:MyRunner"
```

Discovery: `importlib.metadata.entry_points(group="factory.runners")`

- Built-in runners (`claude`, `bob`, `codex`, `opencode`) registered in `_RUNNERS` dict
- Entry points loaded once via `_load_entrypoints()` with `_entrypoints_loaded` guard
- Built-in runners take precedence on name collision
- Load failures logged at debug level, do not crash
- CLI choices generated dynamically from `get_available_runners().keys()`

## Capability Matrix

### E2E Tested (22 tests, all PASS, real API calls)

| Test | Claude | Bob | Codex | OpenCode |
|------|--------|-----|-------|----------|
| Agent invocation (invoke_agent) | PASS | PASS | PASS | PASS |
| Builder makes code changes | PASS | PASS | PASS | PASS |
| Output captured to .factory/reviews/ | PASS | PASS | PASS | PASS |
| Cross-runner parity | PASS | PASS | PASS | PASS |
| Timeout handling | PASS | PASS | PASS | PASS |
| factory agent --runner CLI | PASS | PASS | PASS | PASS |
| Headless produces output | PASS | PASS | PASS | PASS |
| tmux_persist degradation | — | PASS | PASS | PASS |
| Token telemetry | PASS | — | — | — |
| factory eval | PASS | PASS | PASS | PASS |

### Feature Matrix

| Feature | Claude | Codex | Bob | OpenCode |
|---------|--------|-------|-----|----------|
| Headless mode | -p task | codex exec prompt | -p prompt | -p prompt -q |
| System prompt (proper slot) | --append-system-prompt-file | AGENTS.md (project-level only) | Concatenated | Concatenated |
| Model override | --model | --model (API key mode) | Not supported | --model |
| Permissions skip | --dangerously-skip-permissions | --sandbox workspace-write | --yolo | --dangerously-skip-permissions |
| Token telemetry | JSON usage block | None | None | None |
| JSON output | --output-format json | None | None | None |
| Session naming | --name | None | None | None |
| tmux persistence | Yes | Warns + fallback | Warns + fallback | Warns + fallback |
| Invocation ceilings | None | None | usage.py | None |

### Auth Matrix

| Runner | Primary Auth | Fallback | Config Location |
|--------|-------------|----------|-----------------|
| Claude | Vertex AI / API key / OAuth | claude CLI handles it | ~/.claude/ |
| Codex | ChatGPT OAuth (~/.codex/auth.json) | OPENAI_API_KEY (needs tool-use scopes) | ~/.codex/ |
| Bob | ~/.bob/settings.json | BOBSHELL_API_KEY env → .factory/.bob_auth file | ~/.bob/ |
| OpenCode | OPENAI_API_KEY env | Shell sourcing from ~/.zshrc | opencode config |

## System Prompt Handling

### Current Behavior

The factory agent system has two levels of prompts:

1. **Project-level instructions** (CLAUDE.md, AGENTS.md) — read automatically by each CLI from the project directory. All runners handle this natively.

2. **Per-agent role prompts** (e.g., "You are the Researcher agent...") — resolved by `factory/agents/runner.py` via `resolve_prompt(role, project_path)`. This is where runners diverge:

| Runner | How agent prompt is delivered | Quality impact |
|--------|------------------------------|----------------|
| Claude | `--append-system-prompt-file` → system prompt slot | Full — model treats it as system instructions |
| Codex | Concatenated: `"{prompt}\n\n---\n\n## Current Task\n\n{task}"` | Degraded — model sees it as user message |
| Bob | Same concatenation | Degraded |
| OpenCode | Same concatenation | Degraded |

### Mitigation

The concatenation approach works — all 22 e2e tests pass, and agents produce useful output with all runners. The clear separator (`---\n\n## Current Task`) helps models distinguish the system instructions from the task. But Claude will have an edge on complex multi-step agent tasks where system prompt prioritization matters.

### Future Improvements

- **Codex**: Could write agent prompt to a temporary AGENTS.md in the project directory before invocation. Codex reads AGENTS.md automatically and treats it as system-level instructions.
- **OpenCode**: Could create a temporary opencode agent config with the system prompt. OpenCode supports custom agents with configurable system prompts.
- **Bob**: No known mechanism for separate system prompts. Concatenation is the only option.

## Known Limitations

1. **Codex OAuth + OPENAI_API_KEY conflict**: If `OPENAI_API_KEY` is in the env (e.g., set for OpenCode), Codex switches to API key mode. If that key lacks tool-use scopes → 401. The factory handles this by only setting `CODEX_HOME` in API key mode, and the test suite strips `OPENAI_API_KEY` for Codex CLI tests.

2. **Codex model selection**: OAuth mode uses Codex default model (gpt-5.5); model override only works in API key mode.

3. **OpenCode binary PATH**: Installed via `go install` to `~/go/bin/opencode`. Not on system PATH by default. The runner auto-detects common locations.

4. **OpenCode Go vs npm incompatibility**: The OpenCode runner requires the **Go binary** (`go install github.com/opencode-ai/opencode@latest`). The npm package (`opencode-ai`) exposes a different CLI that does not support the `-p`, `-c`, or `-q` flags used by the runner, and will fail silently. The runner performs a runtime compatibility check on first invocation by running `opencode version` and looking for Go-style semver output (e.g. `opencode version v0.0.55`). A warning is logged if the binary appears to be the npm version.

5. **System prompt degradation**: Non-Claude runners concatenate system + task prompts. Works but less effective than proper system prompt slot.

6. **No fallback chains**: If a runner fails, the factory aborts. No automatic failover to another runner.

7. **Bob invocation ceilings**: Bob Shell has no token telemetry, so the factory self-enforces invocation ceilings via `FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE` (default: 8). All invocations logged to `.factory/bob_usage.jsonl`. Ceiling violations abort with an actionable error.

## Dry-Run Mode

Each runner supports a dry-run env var for testing without spending tokens:

| Runner | Env Var | Behavior |
|--------|---------|----------|
| Claude | — | No dry-run (use mocked subprocess in tests) |
| Bob | `FACTORY_BOB_DRY_RUN=1` | Returns stub response, logs usage |
| Codex | `FACTORY_CODEX_DRY_RUN=1` | Returns stub response |
| OpenCode | `FACTORY_OPENCODE_DRY_RUN=1` | Returns stub response |

Stub responses generated by `make_dry_run_result()` in `factory/runners/_subprocess.py`.

## Files

| File | Purpose |
|------|---------|
| factory/models.py | AgentRunRequest, AgentRunResult, AgentUsage models |
| factory/runners/protocol.py | Runner protocol, RunnerMeta |
| factory/runners/__init__.py | Registry, plugin discovery, get_runner() |
| factory/runners/_subprocess.py | Shared subprocess executor, make_dry_run_result |
| factory/runners/_stream.py | Streaming output, ANSI stripping |
| factory/runners/claude.py | ClaudeRunner |
| factory/runners/bob.py | BobRunner with auth + ceilings |
| factory/runners/codex.py | CodexRunner with CODEX_HOME auth isolation |
| factory/runners/opencode.py | OpenCodeRunner with PATH auto-discovery |
| tests/test_runner_e2e.py | 22 e2e tests with real API calls |
| tests/test_runners.py | Unit tests (mocked subprocess, ~63 tests) |
