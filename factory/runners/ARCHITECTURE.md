# Agent Runner Architecture

## Design Principle

The abstraction defines what the **orchestrator** (CEO agent) needs from any coding agent CLI. Each runner figures out how to deliver — natively, via prompt injection ("prompt proxy"), or as a documented no-op.

## Layers

```
RunnerRequest  →  CLIAdapter (base)  →  Runner Implementation  →  subprocess
                      │                      │
                      ├─ _write_system_prompt()    ├─ _build_command()
                      ├─ _inject_prompt_proxy()    ├─ _parse_output()
                      ├─ _build_env()              ├─ _build_env() override
                      ├─ headless()                └─ check_health() override
                      └─ interactive()
```

### RunnerRequest

The single input object for all runner invocations. Contains:

- **Identity**: `system_prompt`, `task`, `cwd`, `role`
- **Prompt management**: `system_prompt_append`, `system_prompt_files`, `append_system_prompt()`
- **Permission control**: `permission_mode` (AUTO/APPROVE_WRITES/APPROVE_ALL)
- **Tool control**: `allowed_tools`, `disallowed_tools`
- **Sandbox**: `sandbox_mode` (NONE/READ_ONLY/WORKSPACE_WRITE/FULL)
- **Resource limits**: `max_turns`, `max_tokens`, `max_cost_usd`
- **Session**: `model`, `session_name`, `timeout`

### CLIAdapter (base class)

Handles subprocess lifecycle. Subclasses implement `_build_command()` and `_parse_output()`.

Key base methods:
- `_write_system_prompt()` — assembles `full_system_prompt` + prompt proxy → temp file
- `_inject_prompt_proxy()` — default: injects ALL unsupported features as prompt instructions
- `headless()` / `interactive()` — spawn subprocess, enforce timeout, parse output

### Prompt Proxy Pattern

When a runner doesn't natively support a feature, we inject it as a system prompt instruction. Example: Codex has no `--allowedTools` flag, so we add "IMPORTANT: You may ONLY use these tools: Read, Grep, Glob."

Runners override `_inject_prompt_proxy()` to skip features they handle natively. Claude overrides it to only proxy `max_tokens` and `max_cost_usd` (everything else is native).

## Capability Matrix

| Feature | Claude Code | Codex CLI | OpenCode |
|---|---|---|---|
| **System Prompt** | | | |
| Set system prompt | `--append-system-prompt-file` | prompt inline | prompt inline |
| Append system prompt | `full_system_prompt` assembly | `full_system_prompt` assembly | `full_system_prompt` assembly |
| System prompt files | file contents joined | file contents joined | file contents joined |
| **Permission Control** | | | |
| AUTO | `--dangerously-skip-permissions` | `--ask-for-approval never` | prompt proxy |
| APPROVE_WRITES | _(default behavior)_ | `--ask-for-approval write` | prompt proxy |
| APPROVE_ALL | _(default behavior)_ | `--ask-for-approval always` | prompt proxy |
| **Tool Control** | | | |
| Allowed tools | `--allowedTools` (native) | prompt proxy | prompt proxy |
| Disallowed tools | `--disallowedTools` (native) | prompt proxy | prompt proxy |
| **Sandbox** | | | |
| NONE | prompt proxy | `--sandbox none` | prompt proxy |
| READ_ONLY | prompt proxy | `--sandbox read-only` | prompt proxy |
| WORKSPACE_WRITE | prompt proxy | `--sandbox workspace-write` | prompt proxy |
| FULL | prompt proxy | `--sandbox full` | prompt proxy |
| **Resource Limits** | | | |
| Max turns | `--max-turns` (native) | prompt proxy | prompt proxy |
| Max tokens | prompt proxy | prompt proxy | prompt proxy |
| Max cost USD | prompt proxy | prompt proxy | prompt proxy |
| **Model & Session** | | | |
| Model override | `--model` | `--model` | `--model` |
| Session resume | `--name` | _(not supported)_ | _(not supported)_ |
| **Observability** | | | |
| Usage stats | stream-json parsing | _(not available)_ | _(not available)_ |
| Execution trace | stream-json parsing | _(not available)_ | _(not available)_ |
| **Other** | | | |
| Health check | `shutil.which` | `shutil.which` + API key | `shutil.which` |
| Dry-run | _(not implemented)_ | `FACTORY_CODEX_DRY_RUN` | _(not implemented)_ |
| Interactive mode | native | native | native |

### Legend

- **native** — runner CLI has a dedicated flag for this feature
- **prompt proxy** — injected as a system prompt instruction via `_inject_prompt_proxy()`
- **_(not available)_** — runner doesn't provide this data; returns `None`
- **_(not supported)_** — feature doesn't exist for this runner; silently ignored

## File Layout

```
factory/runners/
├── __init__.py          # Registry, get_runner(), exports
├── types.py             # RunnerRequest, RunnerResponse, enums, trace types
├── protocol.py          # Runner Protocol (minimal interface)
├── cli_adapter.py       # CLIAdapter ABC (subprocess lifecycle, prompt proxy)
├── registry.py          # RunnerRegistry (register/get/list/check)
├── claude.py            # ClaudeRunner(CLIAdapter) — stream-json parser, v1 compat
├── codex.py             # CodexRunner(CLIAdapter) — sandbox/approval mapping, v1 compat
├── opencode.py          # OpenCodeRunner(CLIAdapter) — JSON output format
├── bob.py               # BobRunner — legacy runner (not CLIAdapter-based)
├── acp_adapter.py       # ACPAdapter(CLIAdapter) — future ACP JSON-RPC upgrade
├── usage_ledger.py      # Append-only JSONL token usage tracking
├── _stream.py           # Subprocess streaming utilities
└── _tmux_persist.py     # tmux session persistence
```
