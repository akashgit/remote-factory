# Agent-Runner Architecture

## Overview

The factory's agent-runner layer is a 4-layer composable system that separates **what to run** (Agent) from **how to run** (Runtime). Inspired by [agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator)'s plugin architecture.

```
AgentLaunchConfig (semantic config — what the caller wants)
       ↓
Agent Protocol (what to run — maps config to CLI flags)
       ↓
Runtime Protocol (how to run — subprocess, tmux, etc.)
       ↓
AgentRunner (compositor — composes Agent × Runtime)
```

## Layer 1: AgentLaunchConfig (`config.py`)

A Pydantic model that captures **semantic operations** — what you want the agent to do — independent of which CLI backend you're using.

```python
class AgentLaunchConfig(BaseModel):
    # Prompt control
    system_prompt: str | None        # Replace default system prompt
    append_system_prompt: str | None # Append to system prompt
    task: str                        # User task / initial message

    # Tool control
    allowed_tools: list[str] | None   # Whitelist: ["Bash", "Edit"]
    disallowed_tools: list[str] | None # Blacklist: ["WebSearch"]

    # Execution control
    model: str | None                # Model override (e.g. "haiku", "o4-mini")
    permissions: PermissionMode      # "permissionless" | "auto-edit" | "suggest"
    timeout: float                   # Max execution time in seconds
    max_budget_usd: float | None     # Spending cap

    # Workspace
    project_path: Path               # Working directory
    add_dirs: list[Path] | None      # Additional accessible directories

    # Session
    mode: LaunchMode                 # "headless" | "interactive"
    session_name: str | None         # For resume/identification
    role: str                        # Agent role (for logging)
```

### Semantic Field Mapping

Each Agent maps these fields to its own CLI flags:

| Field | Claude Code | Codex |
|-------|------------|-------|
| `system_prompt` | `--system-prompt` | Prepended to prompt text |
| `append_system_prompt` | `--append-system-prompt-file` (temp file) | Prepended to prompt text |
| `task` | `-p <task>` (headless) / positional (interactive) | Appended as `## Current Task` section |
| `allowed_tools` | `--allowedTools "Bash Edit"` | N/A (no tool filtering) |
| `disallowed_tools` | `--disallowedTools "WebSearch"` | N/A |
| `model` | `--model` | `--model` / `-m` |
| `permissions=permissionless` | `--dangerously-skip-permissions` | `--dangerously-bypass-approvals-and-sandbox` |
| `max_budget_usd` | `--max-budget-usd` | N/A |
| `add_dirs` | `--add-dir` | `--add-dir` |
| `mode=headless` | `--output-format json` | `codex exec` |
| `mode=interactive` | No `-p`, no `--output-format` | `codex` (no `exec`) |

## Layer 2: Agent Protocol (`protocol.py`)

The `Agent` protocol defines 4 methods — all pure functions that never execute subprocesses:

```python
class Agent(Protocol):
    name: str

    def get_launch_command(self, config: AgentLaunchConfig) -> list[str]:
        """Map semantic config → CLI args. No side effects except temp files."""

    def get_environment(self, config: AgentLaunchConfig) -> dict[str, str]:
        """Build subprocess env vars."""

    def parse_output(self, stdout: str, return_code: int) -> AgentResult:
        """Parse raw stdout → structured result with optional usage telemetry."""

    def preflight(self) -> None:
        """Validate prerequisites (binary exists, auth configured). Raises on failure."""
```

### Implementations

| Class | File | CLI | Key behaviors |
|-------|------|-----|---------------|
| `ClaudeCodeAgent` | `claude.py` | `claude` | Temp file for append_system_prompt, JSON output parsing with AgentUsage extraction |
| `CodexAgent` | `codex.py` | `codex` | Prompt concatenation (no system prompt flag), CODEX_API_KEY→OPENAI_API_KEY mapping |
| `BobShellAgent` | `bob.py` | `bob` | Prompt concatenation, ceiling/usage preflight, `--chat-mode=code` |

### AgentResult

```python
@dataclass
class AgentResult:
    output: str                # Extracted text (Claude: JSON "result" field; Codex: raw stdout)
    return_code: int           # Subprocess exit code
    usage: AgentUsage | None   # Token counts, cost, duration (Claude only; None for Codex/Bob)
```

## Layer 3: Runtime Protocol (`runtime.py`)

Handles the mechanics of executing a command — decoupled from what the command does.

```python
class Runtime(Protocol):
    async def execute(self, cmd, env, cwd, *, timeout, stream_prefix, sanitize) -> tuple[str, int]:
        """Headless execution → (stdout, return_code)."""

    def execute_interactive(self, cmd, env, cwd) -> int:
        """Foreground execution → exit code."""
```

### Implementations

| Class | Description |
|-------|-------------|
| `ProcessRuntime` | Wraps `asyncio.create_subprocess_exec` + `stream_subprocess`. Handles timeout, FileNotFoundError, streaming with prefix. |
| `TmuxRuntime` | Wraps `_tmux_persist.run_in_tmux()`. Falls back to `ProcessRuntime` if tmux unavailable. |

## Layer 4: AgentRunner (`compositor.py`)

Composes any Agent with any Runtime. This is what `get_runner()` returns.

```python
class AgentRunner:
    def __init__(self, agent: Agent, runtime: Runtime | None = None): ...
    async def headless(self, prompt, task, cwd, **kwargs) -> tuple[str, int, AgentUsage | None]: ...
    def interactive_run(self, prompt, task, cwd, **kwargs) -> int: ...
```

### Legacy compatibility

- Implements the old `Runner` protocol signature (same `headless()` / `interactive_run()` kwargs)
- `AgentRunner.from_legacy(runner)` wraps old-style Runner instances (used for BobRunner's complex ceiling tracking)
- `invoke_agent()` in `factory/agents/runner.py` continues to work without changes

### How it works

```
caller: invoke_agent("builder", task, project_path, runner_name="claude")
  ↓
get_runner("claude") → AgentRunner(ClaudeCodeAgent(), ProcessRuntime())
  ↓
AgentRunner.headless(prompt, task, cwd):
  1. Build AgentLaunchConfig from kwargs (prompt → append_system_prompt)
  2. agent.preflight()           → validates claude binary exists
  3. agent.get_launch_command()  → ["claude", "--append-system-prompt-file", ...]
  4. agent.get_environment()     → {PATH: ..., FACTORY_MODEL: "opus", ...}
  5. runtime.execute(cmd, env)   → subprocess with streaming, timeout
  6. agent.parse_output(stdout)  → AgentResult(output="...", usage=AgentUsage(...))
  7. agent.cleanup()             → removes temp prompt files
  ↓
returns (output, return_code, usage)
```

## Registry

```python
# factory/runners/__init__.py
RunnerName = Literal["claude", "bob", "codex"]
RUNNER_CHOICES = get_args(RunnerName)  # DRY — used by all argparse --runner flags

def get_runner(name: str | None = None) -> AgentRunner:
    # Resolves: CLI flag > FACTORY_RUNNER env > config.toml > "claude"
    # Returns AgentRunner(agent, ProcessRuntime())
```

## Adding a new backend

To add a new agent (e.g. Aider):

1. Create `factory/runners/aider.py` with a class implementing the `Agent` protocol:
   - `get_launch_command()` — map config fields to `aider` CLI flags
   - `get_environment()` — set up env vars
   - `parse_output()` — extract structured output
   - `preflight()` — check binary + auth

2. Register in `factory/runners/__init__.py`:
   - Add to `RunnerName` literal: `Literal["claude", "bob", "codex", "aider"]`
   - Add to `_AGENTS` dict
   - `RUNNER_CHOICES` auto-updates (derived from `RunnerName`)

3. Add tests:
   - Unit tests in `tests/test_agent_protocol.py` for each semantic field
   - Integration test in `tests/test_integration_runners.py` with real CLI

No changes needed to `AgentRunner`, `ProcessRuntime`, `invoke_agent()`, or any CLI commands.

## Test tiers

| Tier | File | Count | What it tests | Cost |
|------|------|-------|---------------|------|
| 1 — Unit | `test_agent_protocol.py` | 56 | Command building, env, output parsing per semantic field. No subprocess, no mocks. | Free |
| 2 — Integration | `test_integration_runners.py` | 17 | Real CLI invocations with real model. Headless, model override, output parsing, cleanup, permissions, interactive, preflight. | ~$0.05/run |
| 3 — E2E | `test_e2e_snake.py` | 1 | Full factory pipeline builds a snake game. | ~$1-2/run |

## Design decisions

1. **Protocol over ABC** — Structural typing, no inheritance coupling. Consistent with existing codebase.
2. **Pydantic for config** — `ConfigDict(strict=True, extra="forbid")` matches project convention. Catches typos at construction time.
3. **Semantic fields over raw flags** — The config models what you want, not how to get it. Each backend maps independently.
4. **ProcessRuntime as default** — TmuxRuntime validates the abstraction but ProcessRuntime handles 99% of cases.
5. **Agent-specific state stays in agent** — Bob's ceiling tracking, Codex's auth mapping — not in the protocol.
6. **Backwards-compatible migration** — `invoke_agent()` signature unchanged. All callers work without modification.
