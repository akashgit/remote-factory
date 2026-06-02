# RFC: Runner Abstraction v2

**Status:** Approved
**Author:** Factory CEO + Human
**Date:** 2026-06-02

## Problem

The factory supports 3 agent backends (Claude Code, Bob Shell, Codex) through a thin `Runner` protocol that returns `tuple[str, int]`. The landscape has exploded to 15+ coding agent CLIs. The current abstraction is too thin:

1. **No structured response.** `tuple[str, int]` loses token usage, cost, session ID, and execution trace data.
2. **No capability negotiation.** Callers don't know what a runner supports (model override, session resume, traces).
3. **No execution traces.** The factory can't see what tool calls the agent made, what files it touched, or what commands it ran — only the final text output.
4. **Duplicated prompt assembly.** All three runners concatenate the same prompt format independently.
5. **No health checks.** No way to verify a runner is installed and authenticated before dispatching work.

## Decision

### Dual-Path Adapter Architecture

Two adapter types, one protocol:

**Type A — Direct CLI adapter.** Spawns the agent CLI as a subprocess, parses its native structured output (Claude's `stream-json`, Codex's `--json`, OpenCode's `--format json`) into `RunnerResponse`. For agents with rich CLI output formats.

**Type B — ACP client adapter.** Uses the [Agent Client Protocol](https://github.com/agentclientprotocol/agent-client-protocol) Python SDK (`pip install agent-client-protocol`) to communicate with any ACP-compatible agent via JSON-RPC over stdio. ONE adapter implementation serves many agents. ACP provides execution traces (tool calls, file diffs, usage) for free via `session_update` callbacks.

### Why ACP

ACP is the emerging standard for editor-to-agent communication (backed by Zed + JetBrains, 170+ compatible agents). Its data model includes exactly what we need: `ToolCall` with `ToolKind` (read/edit/execute/search/think), `ToolCallLocation` (file + line), `Diff` (old/new text), `UsageUpdate` (tokens/cost), and permission requests (auto-approved in headless mode).

Many agents already support it: OpenCode (`opencode acp`), Codex (`codex acp`), Qwen Code (`qwen acp`), Kimi (`kimi acp`), Goose (native ACP server).

### Why Not ACP Only

Claude Code doesn't have `claude acp` yet — it uses `--output-format stream-json` which is equally rich but proprietary. We need a direct CLI adapter for Claude.

### v1 Scope

| Agent | Adapter type | Invocation | Trace data |
|-------|-------------|------------|------------|
| **Claude Code** | Direct CLI (Type A) | `claude --output-format stream-json -p ...` | Full — parsed from JSONL |
| **Codex** | ACP client (Type B) | `spawn_agent_process("codex", "acp")` | Full — from session_update |
| **OpenCode** | ACP client (Type B) | `spawn_agent_process("opencode", "acp")` | Full — from session_update |

### Post-v1 (Optional)

| Agent | Adapter type | Invocation | Trace data | Notes |
|-------|-------------|------------|------------|-------|
| **Bob Shell** | Direct CLI (Type A) | `bob -p ...` | None — no structured output | Migrate after v1 is stable. Existing adapter continues to work unchanged until then. |
| **Qwen Code** | ACP client (Type B) | `spawn_agent_process("qwen", "acp")` | Full | One-liner on top of ACPAdapter |
| **Goose** | ACP client (Type B) | `spawn_agent_process("goose", "acp")` | Full | Native ACP server |
| **Aider** | Direct CLI (Type C) | `aider --message ... --yes` | None | No ACP, no structured output |

## Abstraction

### Types

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Capabilities ──────────────────────────────────────────────

class RunnerCapability(Enum):
    MODEL_OVERRIDE = "model_override"
    SESSION_RESUME = "session_resume"
    STRUCTURED_OUTPUT = "structured_output"
    STREAMING = "streaming"
    INTERACTIVE = "interactive"
    DRY_RUN = "dry_run"
    SANDBOXING = "sandboxing"
    ACP = "acp"
    EXECUTION_TRACE = "execution_trace"


@dataclass
class RunnerInfo:
    name: str                                  # "claude", "codex", "opencode"
    display_name: str                          # "Claude Code", "OpenCode"
    version: str | None = None
    capabilities: set[RunnerCapability] = field(default_factory=set)


# ── Request ───────────────────────────────────────────────────

@dataclass
class RunnerRequest:
    prompt: str                                # Fully assembled (system + playbook + task)
    cwd: str                                   # Working directory
    timeout: int = 300
    model: str | None = None
    session_name: str | None = None
    role: str | None = None                    # Agent role (for logging prefix)
    skip_permissions: bool = True
    env_overrides: dict[str, str] = field(default_factory=dict)


# ── Usage ─────────────────────────────────────────────────────

@dataclass
class UsageStats:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    duration_seconds: float | None = None
    model_used: str | None = None


# ── Execution Trace ───────────────────────────────────────────

class ToolKind(Enum):
    """Aligned with ACP's ToolKind for interop."""
    READ = "read"
    EDIT = "edit"
    DELETE = "delete"
    SEARCH = "search"
    EXECUTE = "execute"
    THINK = "think"
    FETCH = "fetch"
    OTHER = "other"


class ToolCallStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class FileLocation:
    path: str
    line: int | None = None


@dataclass
class ToolCallTrace:
    """One tool invocation. The atom of observability."""
    tool_name: str                             # "Read", "Bash", "Edit"
    kind: ToolKind
    status: ToolCallStatus = ToolCallStatus.COMPLETED
    input_summary: str | None = None           # Truncated (first 200 chars)
    output_summary: str | None = None
    locations: list[FileLocation] = field(default_factory=list)
    duration_ms: int | None = None
    error: str | None = None


@dataclass
class AgentStep:
    """One LLM inference round (turn/step).
    Maps to: Claude assistant+user event pair, Codex turn, OpenCode step."""
    step_index: int
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    reasoning: str | None = None
    output_text: str | None = None
    usage: UsageStats | None = None


@dataclass
class ExecutionTrace:
    """Full execution trace from a runner invocation."""
    steps: list[AgentStep] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    commands_executed: list[str] = field(default_factory=list)
    thinking_blocks: list[str] = field(default_factory=list)
    sub_agent_traces: list["ExecutionTrace"] = field(default_factory=list)


# ── Response ──────────────────────────────────────────────────

@dataclass
class RunnerResponse:
    output: str
    exit_code: int
    usage: UsageStats | None = None
    trace: ExecutionTrace | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

### Protocol

```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class Runner(Protocol):

    @property
    def info(self) -> RunnerInfo: ...

    async def check_health(self) -> tuple[bool, str]: ...

    async def headless(self, request: RunnerRequest) -> RunnerResponse: ...

    def interactive(self, request: RunnerRequest) -> RunnerResponse: ...
```

### Prompt Assembly (Centralized)

Moved out of runners into `agents/runner.py`:

```python
def assemble_prompt(system_prompt: str, playbook: str | None,
                    user_profile: str | None, task: str) -> str:
    parts = [system_prompt]
    if playbook:
        parts.append(f"---\n\nBehavioral Playbook (auto-evolved)\n\n{playbook}")
    if user_profile:
        parts.append(f"---\n\nUser Profile\n\n{user_profile}")
    parts.append(f"---\n\n## Current Task\n\n{task}")
    return "\n\n".join(parts)
```

Runners receive `request.prompt` as a single pre-assembled string.

## Intermediate State Model

During execution, each runner goes through multiple LLM calls, each producing tool calls. The factory captures these as `AgentStep` objects. Here's what each runner emits and when:

### Claude Code (`--output-format stream-json`)

**Real-time JSONL stream.** Events fire as the agent works.

```
system/init              → tools, model, session_id
assistant (tool_use)     → agent wants to call Read("file.py")     ← WE SEE THIS
user (tool_result)       → file contents returned                  ← WE SEE THIS
assistant (tool_use)     → agent wants to call Edit("file.py")     ← WE SEE THIS
user (tool_result)       → "file edited"                           ← WE SEE THIS
assistant (tool_use)     → agent wants to call Bash("pytest")      ← WE SEE THIS
user (tool_result)       → "3 passed"                              ← WE SEE THIS
assistant (text)         → "Done. Fixed the import."               ← final text
result                   → cost, tokens, duration, session_id      ← summary
```

Every tool call is visible with full input and output. Parallel tool calls share the same `message.id`. Sub-agent calls have `parent_tool_use_id`. No in-progress state — we see the request and the result, not "tool is currently running."

### Codex (`codex exec --json` or ACP)

**Via `--json`:** Real-time JSONL. `item.started` fires BEFORE tool execution (`status: "in_progress"`), giving live in-progress state. `turn.completed` gives per-turn token usage. `file_change` gives paths but no diffs.

**Via ACP (`codex acp`):** Same data, richer format. `ToolCall` events with `ToolKind`, `ToolCallLocation`, `Diff` content. `UsageUpdate` for tokens/cost. Permission requests auto-approved.

### OpenCode

**Via `run --format json`:** Real-time JSONL. `tool_use` fires only AFTER completion (no in-progress state). `step_finish` gives per-step cost and tokens.

**Via ACP (`opencode acp`):** Full ACP event stream with in-progress tool calls via `ToolCallUpdate`. Richest data.

**Via `serve` (HTTP/SSE):** 19 event types including `message.part.updated` for live tool progress. Richest stream but requires running a server.

### Capture Strategy

| Runner | Capture method | When data arrives | In-progress visibility |
|--------|---------------|-------------------|----------------------|
| Claude | Parse `stream-json` JSONL from stdout | Real-time (line by line) | No — see request + result only |
| Codex (ACP) | ACP `session_update` callback | Real-time | Yes — `ToolCallStatus.IN_PROGRESS` |
| OpenCode (ACP) | ACP `session_update` callback | Real-time | Yes — `ToolCallUpdate` events |
| Bob | Raw text stdout | End of execution | None |

**Phase 1 (post-hoc):** Parse complete stdout/ACP events after subprocess exits. Populate `RunnerResponse.trace`. No streaming. This is sufficient for CEO review and ACE learning.

**Phase 2 (streaming):** Refactor `_stream.py` to yield parsed events as they arrive. Enables dashboard "Builder is running pytest..." and live progress. Requires async iterator pattern.

## How the Factory Uses Traces

### CEO Review Enrichment

Instead of reading the full PR diff, the CEO sees:

```markdown
## Builder Trace Summary
- Read 7 files (factory/runners/*.py, factory/models.py, tests/test_runners.py)
- Edited 3 files (factory/runners/protocol.py, factory/runners/claude.py, factory/runners/__init__.py)
- Ran 4 commands: pytest (FAIL), pytest (PASS after fix), ruff check (PASS), mypy (PASS)
- 23 tool calls, 2 test retries
- Cost: $0.12 (18k input, 4k output tokens)
```

### ACE Pattern Learning

Correlate tool call patterns with keep/revert outcomes:

```
"Builder runs tests before committing → 78% keep rate"
"Builder edits 5+ files without testing → 62% revert rate"
"Builder uses Think before complex edits → 85% keep rate"
```

### Trace Persistence

Traces are saved to `.factory/experiments/NNN/trace.json`. CLI command `factory trace <project> --exp N` prints a summary.

## File Plan

```
factory/runners/
├── types.py             # NEW: all types (RunnerRequest, RunnerResponse, ExecutionTrace, etc.)
├── protocol.py          # REWRITE: Runner protocol using new types
├── registry.py          # NEW: RunnerRegistry class (replaces __init__.py logic)
├── cli_adapter.py       # NEW: base class for direct CLI adapters (shared subprocess logic)
├── acp_adapter.py       # NEW: ACP client adapter using agent-client-protocol SDK
├── claude.py            # REWRITE: Claude CLI adapter, parses stream-json into ExecutionTrace
├── codex.py             # REWRITE: thin wrapper — ACPAdapter("codex", "acp") + health check
├── opencode.py          # NEW: thin wrapper — ACPAdapter("opencode", "acp") + health check
├── bob.py               # UNCHANGED in v1: migrated post-v1 after new protocol is stable
├── _stream.py           # UPDATE: add JSONL event parsing alongside raw streaming
├── usage_ledger.py      # NEW: unified usage log (.factory/usage.jsonl)
└── __init__.py          # UPDATE: use RunnerRegistry, register all adapters
```

Changes to existing files:

```
factory/agents/runner.py # UPDATE: use RunnerResponse, centralize assemble_prompt(),
                         #         save trace to .factory/experiments/NNN/trace.json
factory/models.py        # UPDATE: add trace-related types if needed for ExperimentRecord
pyproject.toml           # UPDATE: add optional acp dependency
```

### Dependency

```toml
[project.optional-dependencies]
acp = ["agent-client-protocol>=0.10.0"]
```

The factory works without ACP installed (Claude + Bob via direct CLI). Installing `factory[acp]` enables Codex/OpenCode/Qwen via the ACP adapter. The ACP adapter raises a clear error at import time if the SDK is missing.

## Implementation Plan

### Phase 1: Core Types and Protocol

**Files:** `factory/runners/types.py` (new), `factory/runners/protocol.py` (rewrite)

- Define all types: `RunnerCapability`, `RunnerInfo`, `RunnerRequest`, `UsageStats`, `ToolKind`, `ToolCallStatus`, `FileLocation`, `ToolCallTrace`, `AgentStep`, `ExecutionTrace`, `RunnerResponse`
- Rewrite `Runner` protocol with `@runtime_checkable`, new return types, `check_health()`
- Add `assemble_prompt()` to `factory/agents/runner.py`
- Unit tests for types and prompt assembly

### Phase 2: CLI Adapter Base + Claude

**Files:** `factory/runners/cli_adapter.py` (new), `factory/runners/claude.py` (rewrite)

- `CLIAdapter` base class: shared subprocess spawning, timeout handling, streaming, env setup
- `ClaudeRunner` implementation:
  - `check_health()`: verify `claude` binary exists
  - `headless()`: run with `--output-format stream-json`, parse JSONL into `RunnerResponse` + `ExecutionTrace`
  - Map Claude tool names → `ToolKind`: Read→READ, Edit→EDIT, Bash→EXECUTE, Grep/Glob→SEARCH, Write→EDIT
  - Extract `UsageStats` from `result` event (cost_usd, tokens, duration)
  - Extract `session_id` for resume
- Migrate `factory/agents/runner.py` to use `RunnerResponse` instead of `tuple[str, int]`
- Tests with mocked subprocess output (sample Claude JSONL fixtures)

### Phase 3: ACP Adapter + Codex + OpenCode

**Files:** `factory/runners/acp_adapter.py` (new), `factory/runners/codex.py` (rewrite), `factory/runners/opencode.py` (new)

- `ACPAdapter` class:
  - Takes `command: list[str]` (e.g., `["codex", "acp"]`, `["opencode", "acp"]`)
  - `check_health()`: try spawning ACP server, send `initialize`, check response
  - `headless()`:
    1. `spawn_agent_process(FactoryACPClient(), *command)`
    2. `conn.initialize(protocol_version=PROTOCOL_VERSION)`
    3. `conn.new_session(cwd=request.cwd)`
    4. `conn.prompt(session_id, [text_block(request.prompt)])`
    5. Collect `session_update` callbacks into `ExecutionTrace`:
       - `agent_message_chunk` → accumulate output text
       - `agent_thought_chunk` → `trace.thinking_blocks`
       - `tool_call` → `ToolCallTrace` with kind, locations, diffs
       - `tool_call_update` → update status on existing trace entry
       - `usage_update` → `UsageStats`
    6. Auto-approve all `request_permission` calls
    7. `conn.close_session()`, terminate process
    8. Return `RunnerResponse` with full trace
- `CodexRunner(ACPAdapter)`: command=`["codex", "acp"]`, health check verifies `CODEX_API_KEY`
- `OpenCodeRunner(ACPAdapter)`: command=`["opencode", "acp"]`, health check verifies binary exists
- Add `agent-client-protocol` to optional deps
- Tests: mock ACP server sending canned JSON-RPC events

### Phase 4: Registry + CLI

**Files:** `factory/runners/registry.py` (new), `factory/runners/__init__.py` (update), `factory/cli.py` (update)

- `RunnerRegistry` class with `register()`, `get()`, `check_all()`, `list_available()`
- Auto-register all runners, gracefully skip ACP runners if SDK not installed
- CLI commands:
  - `factory runners list` — show all registered runners with health status
  - `factory runners check [name]` — health-check one or all runners
- Update `factory agent` and `factory ceo` to use new registry

### Phase 5: Usage Logging + Trace Persistence

**Files:** `factory/runners/usage_ledger.py` (new), `factory/store.py` (update), `factory/cli.py` (update)

- `log_usage()`: append `UsageStats` from every invocation to `.factory/usage.jsonl` (~50 lines). Replaces the Bob-specific `bob_usage.jsonl` with a unified log across all runners. No external platforms — purely local.
- Wire into `agents/runner.py`: after each agent completes, log usage + save trace
- Save traces to `.factory/experiments/NNN/trace.json`
- CLI command: `factory trace <project> --exp N` — print trace summary

### Phase 6: CEO Trace Integration

**Files:** CEO prompt updates, `factory/agents/runner.py` (update)

- After Builder completes, generate trace summary from `ExecutionTrace`
- Include trace summary in Builder review file (`.factory/reviews/builder-latest.md`)
- CEO structured review can reference trace data ("Builder ran tests 2 times before they passed")
- Wire trace patterns into ACE reflector input (for future playbook evolution)

### Post-v1: Bob Migration

**Files:** `factory/runners/bob.py` (update)

- Refactor to return `RunnerResponse` instead of `tuple[str, int]`
- Move ceiling/usage tracking into `check_health()` and `RunnerResponse.usage`
- Remove `project_path` from constructor — pass cycle state via `env_overrides`
- `trace` is always `None` (Bob has no structured output)
- Deprecate `factory/runners/usage.py`, delegate to unified telemetry ledger

**Why post-v1:** Bob works today. It doesn't need the new abstraction to function. Migrating it after the protocol is proven on Claude + Codex + OpenCode is lower risk and avoids disrupting an existing working adapter during the core refactor.

## Migration Path

Backward-compatible at every phase:

- **Phase 1-2:** Internal refactor. `factory agent` and `factory ceo` work identically. `FACTORY_RUNNER` env var and `--runner` flag unchanged. Bob adapter is untouched — it continues using the old `tuple[str, int]` return internally, with a thin compatibility shim in `agents/runner.py` that wraps it into `RunnerResponse`.
- **Phase 3:** New runners are additive. `--runner codex` works via ACP if SDK installed, falls back to current CLI adapter if not.
- **Phase 4:** `factory runners list` is additive.
- **Phase 5-6:** Trace data is additive — CEO review works the same, just has more data.
- **Post-v1:** Bob migrated to new protocol once stable.

## Open Questions

1. **Trace depth.** Summaries (first 200 chars of input/output) by default, full capture opt-in via `FACTORY_TRACE_FULL=1`.
2. **ACP persistent server.** Start/stop per invocation (simpler) vs keep a persistent server running (faster). Start with per-invocation.
3. **Streaming vs post-hoc.** Start with post-hoc (Phase 1-6). Add streaming when dashboard needs live progress (Phase 8, future).
