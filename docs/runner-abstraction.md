# Agent Runner Abstraction — Reference

## Request Fields → CLI Flag Mapping

Every `Request` field is handled by every runner. "Native" means a real CLI flag; "proxy" means emulated via prompt injection or workaround.

| Request Field | Type | Claude Code | Codex (0.136.0) | Notes |
|---|---|---|---|---|
| `prompt` | `str` | `--append-system-prompt-file <tmpfile>` | positional arg to `codex exec` | Claude writes to temp file; Codex inlines in command |
| `task` | `str` | `-p <task>` | folded into prompt arg | Claude separates prompt/task; Codex combines them |
| `cwd` | `Path` | subprocess `cwd=` | subprocess `cwd=` | Both use subprocess working directory |
| `timeout` | `float` | `asyncio.wait_for()` | `asyncio.wait_for()` | Enforced by the base class `run()` lifecycle |
| `model` | `str \| None` | `--model <model>` | `--model <model>` | Native on both |
| `skip_permissions` | `bool` | `--dangerously-skip-permissions` | `--sandbox workspace-write` | Claude has a single flag; Codex uses sandbox levels |
| `role` | `str` | informational only | controls sandbox level | CEO gets `danger-full-access` for nesting |
| `session_name` | `str \| None` | `--name <name>` | not supported | Claude only; enables `/resume` |
| `tmux_persist` | `bool` | tmux window via `_tmux_persist` | not supported | Claude only |
| `allowed_tools` | `list[str] \| None` | `--allowedTools <tool1> <tool2>` | **proxy**: injected into prompt | Claude native; Codex gets "You may ONLY use: ..." |
| `disallowed_tools` | `list[str] \| None` | `--disallowedTools <tool1> <tool2>` | **proxy**: injected into prompt | Claude native; Codex gets "You must NOT use: ..." |
| `permission_mode` | `str \| None` | `--permission-mode <mode>` | `--sandbox <level>` | Claude has 6 modes; Codex maps `bypassPermissions` → `danger-full-access` |
| `max_budget_usd` | `float \| None` | `--max-budget-usd <amount>` | accepted silently | Claude native; Codex has no budget enforcement |
| `effort` | `str \| None` | `--effort <level>` | **proxy**: injected into prompt | Claude native (low/medium/high/xhigh/max); Codex gets effort instructions |
| `output_format` | `str \| None` | `--output-format <format>` | `--json` (always JSONL) | Claude: text/json/stream-json; Codex: always JSONL |
| `append_system_prompt` | `str \| None` | `--append-system-prompt <text>` | **proxy**: folded into prompt | Claude native; Codex appends to combined prompt |
| `mcp_config` | `list[str] \| None` | `--mcp-config <config>` (repeatable) | accepted silently | Claude only |

## Capability Matrix

| Capability | Claude Code | Codex | How |
|---|---|---|---|
| `MODEL_OVERRIDE` | native | native | `--model` on both |
| `SESSION_RESUME` | native | no | `--name` / `--resume` (Claude only) |
| `SYSTEM_PROMPT_FILE` | native | proxy | Claude: `--append-system-prompt-file`; Codex: inline in arg |
| `STREAMING` | native | no | Claude streams via subprocess; Codex buffered |
| `INTERACTIVE` | native | native | Both support inherited stdio |
| `SANDBOXING` | no | native | Codex: `--sandbox read-only/workspace-write/danger-full-access` |
| `STRUCTURED_OUTPUT` | native | native | Claude: `--output-format json`; Codex: `--json` (JSONL) |
| `TOOL_FILTERING` | native | proxy | Claude: `--allowedTools/--disallowedTools`; Codex: prompt injection |
| `PERMISSION_MODES` | native | partial | Claude: 6 modes via `--permission-mode`; Codex: 3 sandbox levels |
| `BUDGET_CAP` | native | no | Claude: `--max-budget-usd` |
| `EFFORT_CONTROL` | native | proxy | Claude: `--effort`; Codex: prompt injection |
| `APPEND_SYSTEM_PROMPT` | native | proxy | Claude: `--append-system-prompt`; Codex: folded into prompt |
| `MCP_CONFIG` | native | no | Claude: `--mcp-config` |
| `USAGE_TRACKING` | native | native | Claude: JSON `usage` block; Codex: JSONL `turn.completed` event |
| `NESTING` | native | native | Claude: no conflicts; Codex: CEO gets `danger-full-access` |

## AgentRunner Methods

| Method | Type | Purpose |
|---|---|---|
| `identity` | abstract property | Returns `RunnerIdentity` (name, cli_command, capabilities) |
| `_build_command(request)` | abstract | Maps Request fields to CLI arg list |
| `_parse_response(stdout, stderr, rc)` | abstract | Parses subprocess output into `Response` |
| `_build_env()` | virtual | Builds subprocess environment (strips VIRTUAL_ENV, sets runner-specific vars) |
| `check_health()` | virtual | Checks if CLI binary is on PATH |
| `_warn_unsupported(request)` | virtual | Logs warnings for fields that can't be proxied |
| `_inject_tool_restrictions(prompt, request)` | helper | Injects allowed/disallowed tool instructions into prompt text |
| `_inject_effort_instructions(prompt, effort)` | helper | Injects effort-level instructions into prompt text |
| `_inject_append_system_prompt(prompt, extra)` | helper | Appends system prompt text for runners without native support |
| `run(request)` | concrete | Full subprocess lifecycle: warn → build_command → spawn → stream → parse |
| `headless(...)` | compat shim | Legacy interface, delegates to `run()` |
| `interactive_run(...)` | compat shim | Legacy interface for interactive sessions |

## Nesting Architecture

The factory CEO spawns sub-agents via `factory agent <role>` shell commands. When the runner is codex, this creates nested codex processes:

```
codex exec (CEO, danger-full-access)
  └─ factory agent builder --runner codex
       └─ codex exec (builder, workspace-write)
            └─ writes files to disk
```

**Why `danger-full-access` for CEO**: Codex's `workspace-write` sandbox blocks child processes from starting the codex app-server (fails with `Operation not permitted`). The CEO needs `danger-full-access` so inner codex instances can initialize.

**Runner propagation**: `CodexRunner._build_env()` sets `FACTORY_RUNNER=codex` so sub-agents inherit the runner choice. Without this, sub-agents fall back to `claude`.

## Response Parsing

| Runner | Output Format | Text Extraction | Usage Extraction |
|---|---|---|---|
| Claude Code | Single JSON blob | `data["result"]` | `data["usage"]` → `AgentUsage` |
| Codex | JSONL events | `item.completed` → `item.text` | `turn.completed` → `usage.{input_tokens, output_tokens, cached_input_tokens}` |

### Codex JSONL Event Types

```jsonl
{"type":"thread.started","thread_id":"..."}
{"type":"turn.started"}
{"type":"item.started","item":{"type":"command_execution","command":"...","status":"in_progress"}}
{"type":"item.completed","item":{"type":"agent_message","text":"..."}}
{"type":"item.completed","item":{"type":"command_execution","command":"...","aggregated_output":"...","exit_code":0}}
{"type":"turn.completed","usage":{"input_tokens":N,"cached_input_tokens":N,"output_tokens":N,"reasoning_output_tokens":N}}
```

## Verification Status

| Runner | Installed | CLI Verified | E2E Tests | Build Tests | Nesting Tested |
|---|---|---|---|---|---|
| Claude Code | yes | yes | 11 pass | yes (factory runs) | yes (native) |
| Codex 0.136.0 | yes | yes | 5 pass | 4 pass (fibonacci, fizzbuzz, snake) | yes (danger-full-access) |
| OpenCode | yes | **not yet** | none | none | not tested |
