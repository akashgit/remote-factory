# Agent-Runner Abstraction

**Status:** Draft
**Dependency:** [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator)
**Implementation:** `factory/runners/abstraction.py`

## Problem

There are 15+ coding agent CLIs (Claude Code, Codex, OpenCode, Goose, Aider, Cursor, Windsurf, Kimi, Qwen Code, etc.). An orchestrator that wants to use any of them as a backend needs to solve the same problems for each one:

- How do I set the agent's identity? (system prompt)
- How do I give it a task? (user message)
- How do I control what it's allowed to do? (permissions, sandbox)
- How do I get structured output back? (text, exit code, token usage)
- How do I know if it's even installed? (health check)

Every agent has different CLI flags, different output formats, and different ways of receiving prompts. Without an abstraction, you write N bespoke integrations for N agents — each 200-400 lines of subprocess plumbing, flag mapping, and output parsing.

## Approach: Use ComposioHQ/agent-orchestrator

Rather than building a runner abstraction from scratch, we should **use [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) as a dependency or recycle its runner design**. That project has already solved the multi-agent-CLI orchestration problem and has production-tested runner implementations for multiple agents.

What we take from agent-orchestrator:
- Runner base class and lifecycle management
- Agent subprocess spawning, streaming, and output capture
- Registry pattern for runner discovery and resolution
- Health check and error recovery patterns

What we layer on top:
- **System prompt / task separation** — the factory needs to control agent identity independently from the task (see below)
- **Capability parity enforcement** — every operation that works on ClaudeCodeRunner must also work on CodexRunner and OpenCodeRunner (see below)

## Capability Parity Requirement

**The key methods and capabilities that work on ClaudeCodeRunner MUST also be supported on CodexRunner and OpenCodeRunner.** The factory dispatches the same specialist agents (Builder, Reviewer, Researcher) across runners — if a capability works on Claude but silently fails on Codex, the factory is broken.

### Required capabilities across all v1 runners

| Capability | Claude Code | Codex | OpenCode | How |
|-----------|-------------|-------|----------|-----|
| **System prompt injection** | `--append-system-prompt-file` | Inline via `.prompt` | Inline via `.prompt` | Each runner maps `Request.system_prompt` to its native mechanism |
| **Task delivery** | `-p "task"` | `codex exec "prompt"` | `opencode run "prompt"` | Each runner maps `Request.task` to its native mechanism |
| **Headless execution** | `--output-format stream-json` | `codex exec --json` | `opencode run --format json` | All three support headless with structured output |
| **Permission skip** | `--dangerously-skip-permissions` | `--ask-for-approval never` | Default headless | Each runner maps `Request.skip_permissions` to its native flag |
| **Model override** | `--model X` | `--model X` | `--model X` | All three support this |
| **Health check** | Binary on PATH | Binary + API key | Binary on PATH | Each runner implements `check_health()` |
| **Timeout enforcement** | Process kill after N seconds | Process kill after N seconds | Process kill after N seconds | Base class handles this |
| **Environment isolation** | Strip VIRTUAL_ENV | Strip VIRTUAL_ENV + map API keys | Strip VIRTUAL_ENV | Base class + runner override |

### Operations that MUST produce equivalent results

These are the operations the factory's `invoke_agent()` relies on. If any of these work differently across runners, agents will behave inconsistently:

1. **`run(request) -> Response`** — headless execution with the same Request must produce a Response with `.output` (final text) and `.exit_code` (0 = success) on all runners. The agent should have performed the task described in `request.task` within the working directory `request.cwd`.

2. **System prompt controls agent behavior** — the `request.system_prompt` must actually influence the agent's behavior on all runners. For Claude this goes via `--append-system-prompt-file` (native system prompt). For Codex/OpenCode it's prepended to the user message. The effect should be equivalent: the agent adopts the role defined in the system prompt.

3. **Permission skip enables headless operation** — `request.skip_permissions=True` must result in fully autonomous execution (no interactive approval prompts) on all runners. Claude uses `--dangerously-skip-permissions`, Codex uses `--ask-for-approval never`. If a runner can't skip permissions, it cannot be used for headless factory operation.

4. **Model override selects the LLM** — `request.model` must be passed through to the agent's model selection flag on all runners that declare `MODEL_OVERRIDE`. The factory uses this to run experiments on different models.

### Capabilities that are runner-specific (OK to differ)

| Capability | Notes |
|-----------|-------|
| Session resume | Claude only (`--name` flag). Others start fresh each invocation. |
| Streaming | Nice-to-have. Claude streams via `stream-json`. Others may not. |
| Execution traces | Claude's `stream-json` provides full tool call traces. Others return raw text. |
| Sandboxing | Codex has `--sandbox workspace-write`. Others rely on OS-level isolation. |

These are genuinely optional — the factory adapts its behavior based on declared capabilities.

## Solution

One base class. Three methods to implement. Everything else is shared.

```
                    ┌─────────────────────┐
                    │    AgentRunner       │
                    │  (base class)        │
                    │                      │
                    │  .run(request)       │  ← subprocess lifecycle
                    │  .run_interactive()  │  ← inherited stdio
                    │  .check_health()     │  ← binary + auth check
                    │  ._build_env()       │  ← env isolation
                    └──────────┬──────────┘
                               │
            ┌──────────────────┼──────────────────┐
            │                  │                   │
   ┌────────▼────────┐ ┌──────▼───────┐ ┌────────▼────────┐
   │ ClaudeCodeRunner │ │ CodexRunner  │ │  AiderRunner    │
   │                  │ │              │ │                  │
   │ _build_command() │ │ _build_cmd() │ │ _build_command() │
   │ _parse_response()│ │ _parse_resp()│ │ _parse_response()│
   │ identity         │ │ identity     │ │ identity         │
   └──────────────────┘ └──────────────┘ └──────────────────┘
        ~20 lines            ~25 lines         ~10 lines
```

### What a subclass implements

| Method | Purpose | Example |
|--------|---------|---------|
| `identity` (property) | Name, display name, capabilities | `RunnerIdentity(name="claude", ...)` |
| `_build_command(request, prompt_file=)` | Map request → CLI args | `["claude", "-p", request.task, ...]` |
| `_parse_response(stdout, stderr, exit_code)` | Map CLI output → Response | Parse JSON, extract text |
| `check_health()` (optional override) | Verify binary + auth | Check API key env vars |
| `_build_env(request)` (optional override) | Custom env setup | Map `CODEX_API_KEY` → `OPENAI_API_KEY` |

### What the base class handles

- Subprocess spawn, timeout enforcement, cleanup
- System prompt written to temp file (passed as `prompt_file` to `_build_command`)
- Environment isolation (`VIRTUAL_ENV` stripped, `request.env` merged)
- Interactive mode (inherited stdio, no capture)
- Error handling (binary not found, timeout)

## Core Design Decision: System Prompt vs Task

The most important design decision is separating `system_prompt` from `task` at the request level:

```python
@dataclass
class Request:
    system_prompt: str   # WHO the agent is (role, playbook, constraints)
    task: str            # WHAT to do (user's work request)
    cwd: str             # WHERE to work
    ...

    @property
    def prompt(self) -> str:
        """Combined string for agents that don't separate them."""
        return f"{self.system_prompt}\n\n---\n\n## Current Task\n\n{self.task}"
```

**Why this matters:** Different agents deliver system prompts through completely different mechanisms. The abstraction must preserve this separation so each runner can use its native approach:

| Agent | System prompt delivery | Task delivery |
|-------|----------------------|---------------|
| **Claude Code** | `--append-system-prompt-file <path>` | `-p "task"` |
| **Codex** | Inlined (no separation) | `codex exec "system+task"` |
| **OpenCode** | Inlined (no separation) | `opencode run "system+task"` |
| **Aider** | Not supported (inline only) | `--message "system+task"` |
| **Goose** | Inlined | `-t "system+task"` |

Runners that support native system prompt separation (Claude) use `request.system_prompt` and `request.task` independently in `_build_command()`. Runners that don't (Codex, Aider) use `request.prompt` which combines both.

The `prompt_file` parameter in `_build_command()` provides the system prompt as a temp file path — runners like Claude use this directly with `--append-system-prompt-file`. Others ignore it.

## Why Runners Inherit Their Native Tools

The abstraction does NOT inject tools into the agent. Each coding agent comes with its own tool set:

- **Claude Code:** Read, Edit, Write, Bash, Grep, Glob, WebFetch, Agent, etc.
- **Codex:** file read/write, shell execution, code search
- **Aider:** file editing, git operations, linting
- **Goose:** file operations, shell, browser

These tools are the agent's — they're part of its runtime, not something the orchestrator provides. The system prompt can guide tool usage ("use Bash to run tests before committing"), but the tools themselves are native to each agent.

This is a deliberate contrast to tool-injection approaches (like MCP) where the orchestrator provides tools to the agent. Here, the agent IS the tool — it comes batteries-included.

## Capability Negotiation

Runners declare what optional features they support:

```python
class Capability(Enum):
    MODEL_OVERRIDE = "model_override"        # --model flag
    SESSION_RESUME = "session_resume"        # Named sessions for resume
    SYSTEM_PROMPT_FILE = "system_prompt_file" # File-based system prompt
    STREAMING = "streaming"                  # Real-time output streaming
    INTERACTIVE = "interactive"              # TUI with inherited stdio
    SANDBOXING = "sandboxing"                # Built-in sandbox/permissions
    STRUCTURED_OUTPUT = "structured_output"  # JSON/JSONL parseable output
```

Callers check before using:

```python
runner = get_runner("codex")
if Capability.MODEL_OVERRIDE in runner.identity.capabilities:
    request.model = "gpt-5.4"
# Otherwise: don't pass --model, it would error
```

This prevents runtime errors from passing flags to agents that don't support them.

## Health Checks

Every runner must implement `check_health() -> (bool, str)`:

```python
# Default: binary exists on PATH
async def check_health(self):
    if shutil.which(self._binary):
        return True, "claude found"
    return False, "claude not found in PATH"

# Override to add auth checks:
async def check_health(self):
    ok, msg = await super().check_health()
    if not ok:
        return ok, msg
    if not os.environ.get("CODEX_API_KEY"):
        return False, "CODEX_API_KEY not set"
    return True, "codex ready"
```

Health checks run before dispatching work, catching missing binaries and expired credentials before wasting time on subprocess failures.

## How Each Agent Maps to the Abstraction

### Claude Code (~20 lines)

```python
class ClaudeCodeRunner(AgentRunner):
    @property
    def identity(self):
        return RunnerIdentity(
            name="claude", display_name="Claude Code",
            capabilities={Capability.MODEL_OVERRIDE, Capability.SESSION_RESUME,
                          Capability.SYSTEM_PROMPT_FILE, Capability.STRUCTURED_OUTPUT,
                          Capability.STREAMING, Capability.INTERACTIVE},
        )

    def _build_command(self, request, *, prompt_file=None):
        cmd = ["claude"]
        if prompt_file:
            cmd.extend(["--append-system-prompt-file", prompt_file])
        cmd.extend(["-p", request.task, "--output-format", "stream-json"])
        if request.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if request.model:
            cmd.extend(["--model", request.model])
        if request.session_name:
            cmd.extend(["--name", request.session_name])
        return cmd

    def _parse_response(self, stdout, stderr, exit_code):
        return Response(output=stdout, exit_code=exit_code)
```

Key: system prompt goes via `--append-system-prompt-file` (file), task goes via `-p` (inline). Claude is the only agent that fully separates these.

### Codex (~25 lines)

```python
class CodexCLIRunner(AgentRunner):
    @property
    def identity(self):
        return RunnerIdentity(
            name="codex", display_name="OpenAI Codex",
            capabilities={Capability.MODEL_OVERRIDE, Capability.SANDBOXING},
        )

    async def check_health(self):
        ok, msg = await super().check_health()
        if not ok:
            return ok, msg
        if not (os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY")):
            return False, "CODEX_API_KEY or OPENAI_API_KEY not set"
        return True, "codex ready"

    def _build_command(self, request, *, prompt_file=None):
        cmd = ["codex", "exec", request.prompt,  # .prompt = system + task combined
               "--sandbox", "workspace-write", "--ask-for-approval", "never"]
        if request.model:
            cmd.extend(["--model", request.model])
        return cmd

    def _build_env(self, request):
        env = super()._build_env(request)
        if "OPENAI_API_KEY" not in env and "CODEX_API_KEY" in env:
            env["OPENAI_API_KEY"] = env["CODEX_API_KEY"]
        return env

    def _parse_response(self, stdout, stderr, exit_code):
        return Response(output=stdout, exit_code=exit_code)
```

Key: uses `request.prompt` (combined), custom health check for API key, custom env for key mapping.

### Aider (~10 lines)

```python
class AiderRunner(AgentRunner):
    @property
    def identity(self):
        return RunnerIdentity(name="aider", display_name="Aider")

    def _build_command(self, request, *, prompt_file=None):
        return ["aider", "--message", request.prompt, "--yes"]

    def _parse_response(self, stdout, stderr, exit_code):
        return Response(output=stdout, exit_code=exit_code)
```

Key: no capabilities declared (no model override, no streaming, no structured output). Simplest possible implementation.

## Integration with ComposioHQ/agent-orchestrator

**We should use [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) as a dependency or recycle its runner implementation.** Do not rewrite what they've already built. Specifically:

### What to use from agent-orchestrator

- **Runner base class / subprocess management** — their agent spawning, streaming, output capture, and lifecycle management is production-tested. Use it directly rather than maintaining our own subprocess plumbing.
- **Agent registry** — their pattern for registering and discovering runners by name.
- **Error recovery** — their handling of agent crashes, timeouts, and partial outputs.
- **Existing runner implementations** — if they already have a Codex or OpenCode runner, use or extend it rather than writing from scratch.

### What we add on top

- **System prompt / task separation at the Request level** — the factory needs `system_prompt` and `task` as separate fields because it controls agent identity (role prompt, behavioral playbook) independently from the user's work request. If agent-orchestrator's request model doesn't separate these, wrap it.
- **Capability parity enforcement** — every method that works on the Claude runner must also work on Codex and OpenCode runners (see capability parity table above).
- **Factory-specific integration** — usage logging, trace persistence, review file generation.

### Implementation plan

1. Add `agent-orchestrator` (or its runner module) as a dependency
2. Adapt our `AgentRunner` base class to extend or wrap theirs
3. Ensure our `Request` type maps cleanly to their input format (with system_prompt/task separation preserved)
4. Verify capability parity: run the same test suite against Claude, Codex, and OpenCode runners
5. Remove our own subprocess plumbing in `cli_adapter.py` if agent-orchestrator handles it

## Usage

```python
from factory.runners.abstraction import AgentRunner, Request, Response, RunnerIdentity

# Create a request
request = Request(
    system_prompt="You are a code reviewer. Be thorough and critical.",
    task="Review the changes in PR #42 for security issues.",
    cwd="/path/to/project",
    timeout=600,
    model="claude-sonnet-4-6",
)

# Run with any agent
runner = ClaudeCodeRunner()
ok, msg = await runner.check_health()
if ok:
    response = await runner.run(request)
    print(response.output)
    print(f"Exit: {response.exit_code}")
```

## File Layout

```
factory/runners/
├── abstraction.py       # THIS FILE — the protocol-agnostic base class + examples
├── protocol.py          # Factory-specific Runner protocol (uses factory types)
├── types.py             # Factory-specific types (RunnerRequest, RunnerResponse, etc.)
├── cli_adapter.py       # Factory-specific CLIAdapter (uses _stream.py, factory types)
├── claude.py            # Production ClaudeRunner with full stream-json parser
├── codex.py             # Production CodexRunner with v1 backward compat
├── opencode.py          # Production OpenCodeRunner
├── acp_adapter.py       # ACP client adapter (requires agent-client-protocol SDK)
├── registry.py          # RunnerRegistry for name-based lookup
└── ...
```

`abstraction.py` is the standalone, dependency-free design. The other files are the factory's production implementation that builds on top of it with factory-specific concerns (streaming via `_stream.py`, v1 backward compatibility, trace parsing, usage logging).
