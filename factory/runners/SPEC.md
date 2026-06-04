# Extensible Agent-Runner Abstraction — Specification

## Improvement Goal

Redesign the factory's runner layer from a flat 2-method `Runner` protocol into a layered, extensible agent-runtime architecture — inspired by agent-orchestrator's separation of "what to run" from "how to run." Re-implement Claude Code and Codex as agents following the new abstraction. Validate with real-model integration tests and an e2e test that uses the factory to build a snake game.

## Prior Art

Comparative analysis of [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) identified key patterns:

- **8 plugin slots** (Agent, Runtime, Workspace, Tracker, SCM, Notifier, Terminal, Lifecycle) vs factory's monolithic Runner
- **Agent interface with 16 methods** (getLaunchCommand, getEnvironment, getActivityState, isProcessRunning, getSessionInfo, preflight, preLaunchSetup, postLaunchSetup, etc.) vs factory's 2
- **Clean Runtime/Agent separation** — "how to execute" (tmux, process, docker) decoupled from "what to execute" (claude, codex, aider)
- **AgentLaunchConfig** — structured config object instead of 8+ keyword arguments
- **Plugin module pattern** with manifest, create(), detect() for auto-discovery

## Current State (Before)

The runner layer (`factory/runners/`) uses a `Runner` protocol with exactly 2 methods (`headless`, `interactive_run`) and 1 attribute (`name`). Three implementations: `ClaudeRunner`, `CodexRunner`, `BobRunner`.

**Problems identified:**
1. Command building coupled to execution — `headless()` builds CLI args, sets env, AND runs subprocess in one 60-line method
2. No separation between "what to run" (claude vs codex) and "how to run" (process vs tmux)
3. Output parsing entangled with execution — Claude's JSON parsing inside `headless()`
4. Auth/preflight checks ad-hoc per runner
5. 100% mocked tests — zero real CLI invocations
6. No semantic abstraction — callers must know raw CLI flags
7. Argparse `--runner` choices manually duplicated, codex excluded from 4 commands
8. `runner_name` not propagated through `_run_single_cycle()` and `_chain_modes()`

## Proposed Changes

### 1. AgentLaunchConfig — semantic operations model

**What:** Pydantic model capturing what callers want: system prompt, tool control, permissions, model, budget, workspace dirs — independent of CLI backend.

**How:** Defined in `factory/runners/config.py`. Fields map to backend-specific flags via Agent implementations. `permissions` uses `Literal["permissionless", "auto-edit", "suggest"]`. Legacy `prompt` kwarg maps to `append_system_prompt`.

**Why:** Agent-orchestrator's AgentLaunchConfig pattern eliminates signature bloat and makes the config self-documenting. Semantic fields mean callers never need to know `--dangerously-skip-permissions` vs `--dangerously-bypass-approvals-and-sandbox`.

### 2. Agent protocol — pure command building

**What:** Protocol with `get_launch_command()`, `get_environment()`, `parse_output()`, `preflight()`. Pure functions — no subprocess execution.

**How:** Each method takes `AgentLaunchConfig` and returns data. `ClaudeCodeAgent` maps `allowed_tools` → `--allowedTools`, `system_prompt` → `--system-prompt`, etc. `CodexAgent` maps `system_prompt` → inline concatenation (no flag). `preflight()` replaces ad-hoc `_check_auth()`.

**Why:** Separating command construction from execution means unit tests verify exact CLI commands without mocking. Swapping runtime doesn't require touching agents.

### 3. Runtime protocol — execution mechanics

**What:** `execute()` for headless, `execute_interactive()` for foreground. `ProcessRuntime` wraps asyncio subprocess. `TmuxRuntime` wraps `_tmux_persist`.

**How:** Extracts the subprocess execution code duplicated across ClaudeRunner and CodexRunner — same `create_subprocess_exec` → `stream_subprocess` → timeout → error handling pattern.

**Why:** Eliminates duplication. Two implementations validate the abstraction.

### 4. AgentRunner compositor

**What:** Composes Agent + Runtime. Returned by `get_runner()`. Implements legacy `Runner` interface.

**How:** `headless()` calls `preflight()` → `get_launch_command()` → `get_environment()` → `runtime.execute()` → `parse_output()` → `cleanup()`. `from_legacy()` wraps old Runner instances for backwards compat.

**Why:** Composition over inheritance. Adding new agents requires only the Agent protocol; adding new runtimes requires only the Runtime protocol.

### 5. ClaudeCodeAgent and CodexAgent implementations

**What:** Rewrite runners as thin, testable classes focused on command/env/output. No subprocess code.

**How:** Claude: temp files for append_system_prompt, JSON output parsing, AgentUsage extraction. Codex: prompt concatenation, CODEX_API_KEY mapping, raw output. Updated codex flags: `--dangerously-bypass-approvals-and-sandbox` (replaces old `--sandbox workspace-write --ask-for-approval never`).

### 6. Backwards-compatible migration

**What:** `invoke_agent()` unchanged signature. `get_runner()` returns AgentRunner. DRY argparse choices. `runner_name` propagation.

**How:** `get_runner()` composes Agent + Runtime. Compositor's `headless()` translates kwargs to AgentLaunchConfig. `RUNNER_CHOICES = get_args(RunnerName)` used everywhere. `_run_single_cycle()` and `_chain_modes()` accept `runner_name`.

### 7-9. Three-tier testing

**Tier 1 — Unit (56 tests, `test_agent_protocol.py`):** Every semantic field × both backends. Command building, env, output parsing. No mocks, no subprocess.

**Tier 2 — Integration (17 tests, `test_integration_runners.py`):** Real Claude + Codex CLI with real model. Headless basic, model override, output parsing, cleanup, permissions, interactive command, preflight.

**Tier 3 — E2E (1 test, `test_e2e_snake.py`):** Factory CEO builds a snake game from scratch. Full pipeline: discover → strategy → build → review.

## Success Criteria

1. [x] `Agent` protocol with `get_launch_command()`, `get_environment()`, `parse_output()`, `preflight()`
2. [x] `Runtime` protocol with `ProcessRuntime` and `TmuxRuntime`
3. [x] `AgentRunner` compositor returned by `get_runner()`
4. [x] `ClaudeCodeAgent` and `CodexAgent` implement `Agent` — no subprocess code
5. [x] `invoke_agent()` unchanged signature
6. [x] Semantic config fields: system_prompt, append_system_prompt, allowed_tools, disallowed_tools, model, permissions, max_budget_usd, add_dirs
7. [x] 56 Tier 1 unit tests pass
8. [x] 17 Tier 2 integration tests pass (real Claude + Codex CLI)
9. [x] Tier 3 e2e: factory builds snake game (95-line curses game, valid Python)
10. [x] All 2247 existing tests pass
11. [x] `ruff check` clean

## Scope Boundaries

**In scope:**
- AgentLaunchConfig with semantic fields
- Agent, Runtime protocols and implementations
- AgentRunner compositor
- ClaudeCodeAgent, CodexAgent, BobShellAgent
- ProcessRuntime, TmuxRuntime
- Updated get_runner() and invoke_agent()
- DRY argparse choices, runner_name propagation
- Three-tier tests

**Out of scope:**
- Plugin auto-discovery / manifest system
- Docker/k8s runtime
- Activity monitoring, session resume, workspace hooks
- New backends beyond existing three
- Changes to agent prompts, CEO logic, or CLI signatures
