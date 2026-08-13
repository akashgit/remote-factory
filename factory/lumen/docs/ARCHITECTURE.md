# Factory Architecture

This document describes the code structure under `factory/`, the core Python package of the remote-factory project.

## Overview

The factory is a multi-agent orchestration system built on top of Claude Code. It spawns Claude Code instances as headless subprocesses with role-specific system prompts, manages experiment lifecycles, and uses directed-graph workflows to coordinate agent execution. The codebase can be divided into 7 functional layers.

---

## 1. Data Layer (depended on by everything)

| Module | Lines | Purpose |
|--------|-------|---------|
| `models.py` | 653 | All Pydantic v2 strict models: `ProjectState`, `FactoryConfig`, `EvalProfile`, `ExperimentRecord`, `CompositeScore`, etc. Zero internal dependencies — the root type definition file |
| `events.py` | 107 | Append-only structured event log. `emit_event()` writes to `.factory/events.jsonl`; `load_events()` reads back. Foundation for all observability |
| `state.py` | 100 | Project state detection. `detect_state()` checks git status, `.factory/` directory, and eval profile to return one of 5 `ProjectState` enum values (`NO_REPO` → `HAS_FACTORY`), determining which workflow the CEO follows |

---

## 2. Experiment Lifecycle (core data flow)

This is the central data pipeline:

```
discovery → eval → store → strategy
(discover project) (score) (record experiment) (pick next move)
```

| Module | Purpose |
|--------|---------|
| **`discovery/`** | Project introspection. `introspect.py` detects language/framework → `profile.py` builds an `EvalProfile` (which dimensions to score) → `generate.py` emits an `eval/score.py` scoring script |
| **`eval/`** | Scoring engine. `runner.py` merges three tiers of dimensions: `hygiene.py` (6 hygiene metrics: lint/test/type-check, etc.) + `growth.py` (5 growth metrics: capability surface, experiment diversity, etc.) + user-defined project evals. `scorer.py` (47 lines) computes the final weighted `CompositeScore`. `languages/` has language-specific probes for Python/Go/Node/Rust |
| **`store.py`** | Experiment filesystem store (727 lines). `ExperimentStore` manages the `.factory/` directory: `begin()` creates experiment dirs and records baseline scores; `finalize()` compares before/after, writes verdict. History is append-only to `results.tsv`. Also parses `factory.md` → `FactoryConfig` |
| **`strategy.py`** | FEEC priority heuristic. Classifies hypotheses into Fix > Exploit > Explore > Combine. 3-tier context compression: recent experiments get full text, older ones get one-line summaries, oldest get aggregate stats. Stuck detection triggers after 3+ consecutive same-category reverts |

### Single experiment data flow

```
1. detect_state()         → HAS_FACTORY
2. store.begin()          → create experiments/003/, run eval → eval_before.json
3. CEO spawns Builder     → Builder modifies code → changes.diff
4. store.finalize()       → run eval → eval_after.json → compare → verdict.json (KEEP/REVERT)
5. Append row to results.tsv
6. strategy.categorize()  → select strategy for next round
```

---

## 3. Agent System (execution layer)

| Module | Purpose |
|--------|---------|
| **`agents/runner.py`** | Agent dispatch core. `resolve_prompt()` does 2-tier prompt lookup (project override `.factory/agents/<role>.md` → default `prompts/<role>.md`). `invoke_agent()` passes the resolved prompt to the selected runner, saves output to `.factory/reviews/<role>-latest.md`. Tracks consecutive failure counts |
| **`agents/prompts/*.md`** | 17 role prompts: ceo, researcher, strategist, builder, qa, code_reviewer, health_checker, adversarial_tester, archivist, failure_analyst, refiner, profiler, refactory, skill_reviewer, spec_extractor, spec_annotator, spec_patcher |
| **`agents/plugin.py`** | Installs factory agents as Claude Code plugins (`factory install`) |

---

## 4. Runner Abstraction (interface to Claude Code)

| Module | Purpose |
|--------|---------|
| `runners/protocol.py` | Runner protocol. Defines `RunnerMeta` and the interface all runners must implement |
| `runners/claude.py` | Claude Code backend. `build_command()` constructs `claude -p ... --append-system-prompt-file ...` |
| `runners/bob.py` | Bob Shell backend (316 lines, with token usage ceiling enforcement) |
| `runners/codex.py` | OpenAI Codex backend |
| `runners/opencode.py` | OpenCode backend |
| `runners/_subprocess.py` | Unified `run_subprocess()` — all runners ultimately spawn processes through here |
| `runners/_stream.py` | Stream-JSON output parsing |
| `runners/_background.py` | Background process management |
| `runners/_tmux_persist.py` | tmux session persistence |
| `runners/usage.py` | Token usage accounting and aggregation |

### Call chain

```
agents/runner.py → runners/__init__.py (get_runner) → runners/claude.py → runners/_subprocess.py
                                                    → runners/bob.py    → runners/_subprocess.py
                                                    → runners/codex.py  → runners/_subprocess.py
```

---

## 5. Workflow Orchestration

| Module | Purpose |
|--------|---------|
| `workflow/primitives.py` | Graph node types: `AgentNode`, `GateNode`, `ForkNode`, `JoinNode`, `FnNode`, `Study`, plus `Edge` and `Verdict` |
| `workflow/definitions.py` | All workflow definitions. Each mode (build, improve, research, meta, etc.) is a function returning a `Workflow` object (a DAG) |
| `workflow/executor.py` | Headless executor. `WorkflowExecutor` walks the DAG — spawns agents at `AgentNode`, asks CEO for PROCEED/RELOOP/HALT at `GateNode` |
| `workflow/skill_export.py` | DAG-to-prose converter. Transforms workflow graphs into `SKILL.md` markdown files that the CEO reads and follows inside Claude Code |
| `workflow/deep_qa.py` | Deep QA subgraph (health_checker → code_reviewer → gate → adversarial_tester) |
| `workflow/guard.py` | Pre/post-check guards (hard constraint enforcement) |
| `workflow/context.py` | Workflow runtime context |
| `workflow/validation.py` | Graph structure validation (dead nodes, broken edges, etc.) |
| `workflow/lint.py` | Workflow definition linting |
| `workflow/contributed/` | Community-contributed benchmark workflows (swebench, programbench, featurebench, etc.) |

### Two execution modes from the same graph

- **Headless:** `WorkflowExecutor` walks the DAG programmatically, calling `agents/runner.py` directly
- **Interactive:** `skill_export.py` converts the DAG to a `SKILL.md` prose playbook; the CEO agent reads it at runtime as step-by-step instructions

---

## 6. Supporting Subsystems

| Module | Purpose |
|--------|---------|
| `ace/` | **Adaptive Context Engine** — playbook evolution. `reflector.py` extracts patterns from experiment history; `curator.py` versions playbooks; `injector.py` injects evolved playbooks into agent prompts |
| `registry.py` | Global project registry at `~/.factory/registry.json`. Projects self-register on first `begin()` |
| `insights.py` | Cross-project analysis — extracts patterns from experiment histories across all registered projects |
| `report.py` | Performance report generation — consolidates CEO verdicts and observations into `.factory/performance_report.json` |
| `analysis.py` | Experiment comparison (`diff`) and FEEC analysis (`explain`) |
| `adversarial.py` | GAN-style adversarial eval state machine — Builder vs adversarial_tester attack/defend loop with phase transitions and hysteresis |
| `ceo_completion.py` | CEO completion guard — auto-respawns CEO on premature exit (max 5 retries, 24h staleness) |
| `checkpoint.py` | CEO state snapshots for crash recovery |
| `clean_pr.py` | Clean PR Mode — strips `.factory/` and other non-essential artifacts before pushing PRs to external repos |
| `study.py` | Codebase analysis (1202 lines) — AST-level function/class/complexity statistics |
| `profile.py` | User profiling — extracts working style and decision patterns from experiment history |

---

## 7. Integrations & Periphery

| Module | Purpose |
|--------|---------|
| `cli/` | CLI entry point. Split by function: `ceo.py` (main flow), `agents.py` (agent invocation), `store.py` (experiment management), `eval_cmds.py` (eval), `backlog.py`, `review.py`, etc. |
| `dashboard/` | FastAPI web UI with SSE event streaming — real-time project status display |
| `mcp_server.py` | MCP stdio server — exposes factory operations (`get_score`, `list_experiments`, `get_status`, `list_projects`) for external Claude Code sessions |
| `notify/telegram.py` | Telegram notifications |
| `obsidian/` | Obsidian vault integration for long-term knowledge archival |
| `visualizer/` | Experiment and score visualization tools |
| `user_config.py` | `~/.factory/config.toml` management with 5-tier precedence (CLI flag > env var > profile credential > config.toml > hardcoded default) |
| `telemetry.py` | Token usage telemetry and cost tracking |

---

## Module Dependency Graph (simplified)

```
                    models.py  (pure data definitions, no dependencies)
                       ↑
         ┌─────────────┼────────────────┐
         │             │                │
     state.py      store.py        eval/
         │         ↗       ↘          │
         │   strategy.py   registry.py│
         │                            │
         └──────────┬─────────────────┘
                    │
              agents/runner.py  ←── ace/injector.py
                    │
              runners/*  (claude.py, bob.py, codex.py...)
                    │
             _subprocess.py  →  actual `claude -p ...` process
                    ↑
              workflow/
         executor.py ──→ agents/runner.py (headless mode)
         skill_export.py ──→ SKILL.md (interactive mode, read by CEO)
```

All core data flows through the `.factory/` directory: `store.py` writes, `eval/` scores, `strategy.py` analyzes, `agents/runner.py` executes, `events.py` records, and all artifacts are archived under per-experiment directories.
