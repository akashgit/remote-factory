# Remote Factory — Behavioral Specification

> **Status:** Machine-generated · **RFC 2119 keywords** apply throughout
> **Scope:** Python 3.11+ · Pydantic v2 strict models · async/await
> **Entry point:** `factory.cli:main` → `factory` script (pyproject.toml)

---

## 1  Problem Statement

Software projects require continuous quality improvement — fixing bugs, adding features, hardening tests, refactoring — but the cognitive overhead of detecting what to improve, prioritizing hypotheses, implementing changes, evaluating results, and archiving learnings is high. Manual iteration is slow, inconsistent, and scales poorly across multiple codebases.

Remote Factory is a **domain-agnostic multi-agent software evolution harness**. It automates the scientific improvement loop — observe, hypothesize, experiment, evaluate, archive — by orchestrating specialist AI agents through typed workflow graphs. A CEO agent owns the full lifecycle; specialist agents (Researcher, Strategist, Builder, QA, Archivist, and others) perform scoped tasks under the CEO's coordination.

---

## 2  Goals

1. **Autonomous improvement cycles** — detect project state, select the correct workflow, run experiments, and keep or revert changes without human intervention.
2. **Measurable quality** — every change MUST be evaluated by a composite scoring system (hygiene + growth + project-specific dimensions) before it can be kept.
3. **Non-overridable safety gates** — the precheck system MUST enforce hard constraints that the CEO agent cannot override; a single failure causes mandatory revert.
4. **Runner-agnostic execution** — the factory MUST support multiple CLI backends (Claude, Bob, Codex, OpenCode) via a pluggable runner abstraction.
5. **Cross-project intelligence** — experiment outcomes MUST be aggregated across managed projects to inform strategy via ACE (Autonomous Continuous Evolution).
6. **Self-improvement** — in meta mode, the factory MUST be capable of improving its own agents, prompts, and playbooks using the same experiment loop.

## 3  Non-Goals

- The factory MUST NOT call LLM APIs directly — it delegates to CLI backends.
- The factory MUST NOT modify source code itself — it spawns Builder agents that make changes.
- The factory MUST NOT serve as a general-purpose CI/CD system — it is an improvement loop, not a deployment pipeline.
- The factory MUST NOT require a specific cloud provider or authentication method beyond what the selected runner supports.

---

## 4  Design Philosophy

1. **Observe → Hypothesize → Experiment → Evaluate → Archive (OHEEA):** Every improvement cycle follows this scientific loop. The factory MUST NOT skip evaluation or archival.
2. **FEEC Priority:** Hypotheses MUST be classified and ranked as Fix > Exploit > Explore > Combine. Lower-category hypotheses receive higher priority.
3. **Keep/Revert Binary:** Every experiment ends with a keep or revert verdict. There is no partial acceptance. Errors are treated as reverts.
4. **Layered Architecture:** Pure CLI tools (Layer 1) → Workflow graph engine (Layer 2) → CEO agent (Layer 3) → Specialist agents (Layer 4). Each layer has a clear contract boundary.
5. **Graceful Degradation:** External integrations (GitHub, Obsidian, Langfuse, Telegram) MUST degrade silently — never crash the host process.

---

## 5  Project Identity

| Property | Value |
|---|---|
| Name | `remote-factory` |
| Version | `0.2.0` |
| Language | Python 3.11+ |
| Build system | Hatchling |
| Package manager | `uv` |
| License | MIT |
| Entry point | `factory = "factory.cli:main"` |
| Core dependencies | pydantic, structlog, fastapi, uvicorn, mcp, pyyaml, filelock, networkx, langfuse |

---

## 6  Architecture Overview

The factory is a four-layer system. Each layer has strict upward dependencies only — lower layers MUST NOT import from higher layers.

### 6.1  Layer 1 — Python CLI (`factory/`)

Pure tools that make no decisions. The CLI is the sole entry point for all factory operations.

- `factory/cli/_main.py` builds the argument parser and dispatches to `cmd_*` handlers
- 60+ subcommands organized into 8 groups: Entry Points, Project Setup, Experiment Lifecycle, Project Intelligence, Backlog & Refinement, Knowledge & Archive, Self-Evolution, Configuration
- No subcommand MAY make autonomous decisions — each performs a deterministic operation

[[graph:community:CLI]]

### 6.2  Layer 2 — Workflow Graph Engine (`factory/workflow/`)

All factory modes are defined as directed graphs of typed nodes.

- `primitives.py` defines the node types: `AgentNode`, `FnNode`, `GateNode`, `ForkNode`, `JoinNode`, `Study`
- `definitions.py` constructs named `Workflow` instances with trigger conditions
- `executor.py` walks the DAG deterministically — dispatching agents, evaluating gates, handling reloops
- `skill_export.py` converts graphs to Claude Code `SKILL.md` files for the CEO agent

The same graph definition produces two execution formats:
- **Headless:** `WorkflowExecutor` walks the DAG via `factory workflow run <name>`
- **Interactive:** Exported SKILL.md files guide the CEO agent step-by-step

[[graph:community:WorkflowEngine]]

### 6.3  Layer 3 — CEO Agent

The CEO prompt (`factory/agents/prompts/ceo.md`) plus mode-specific playbooks (`skills/workflow-*/SKILL.md`) form the orchestration layer. The CEO:

1. Detects project state via `factory detect`
2. Selects the appropriate workflow mode
3. Spawns specialist agents via `factory agent <role>`
4. Manages the experiment lifecycle via `factory begin` / `factory finalize`
5. Enforces Sacred Rules (mandatory QA, precheck gates, archival)

[[graph:factory.ceo_completion]]

### 6.4  Layer 4 — Specialist Agents

Eight specialist Claude Code subprocesses, each with a scoped prompt:

| Role | Responsibility | Model | Timeout |
|---|---|---|---|
| `researcher` | Domain research, backlog assessment | sonnet | 600s |
| `strategist` | Hypothesis generation, FEEC ranking | opus | 600s |
| `builder` | Code implementation | opus | 1200s |
| `health_checker` | Test/lint/type check execution | opus | 600s |
| `code_reviewer` | Code review and style analysis | opus | 900s |
| `adversarial_tester` | Adversarial QA, edge case testing | opus | 1800s |
| `failure_analyst` | Research-mode failure categorization | opus | 600s |
| `archivist` | Experiment learnings and knowledge capture | haiku | 300s |
| `refiner` | Post-cycle user-directed refinements | opus | 600s |

Agent prompt resolution follows a two-tier lookup: project override (`.factory/agents/<role>.md`) → factory default (`factory/agents/prompts/<role>.md`). Evolved playbooks from `~/.factory/playbooks/<role>.md` are auto-injected.

[[graph:factory.agents.runner]]

---

## 7  Domain Model

### 7.1  Core Entities

| Entity | Module | Invariants |
|---|---|---|
| `ProjectState` | `models` | Enum of exactly 5 values: `NO_REPO`, `REPO_INCOMPLETE`, `NO_FACTORY`, `EVALS_PENDING_REVIEW`, `HAS_FACTORY` |
| `FactoryConfig` | `models` | Strict Pydantic; `ConfigDict(strict=True, extra="forbid")`; parsed from `factory.md` via `ExperimentStore.reparse_config()` |
| `EvalProfile` | `models` | `tier` ∈ {`explicit`, `discovered`, `researched`, `fallback`}; dimension weights MUST sum to 1.0 within a tier; `human_reviewed` defaults to `False` |
| `ExperimentRecord` | `models` | `verdict` ∈ {`keep`, `revert`, `error`}; `delta` auto-computed as `score_after − score_before` when both present |
| `CompositeScore` | `models` | `passed` MUST be `False` whenever `guard_violations` is non-empty, regardless of `total` |
| `Hypothesis` | `models` | `description`, `rationale`, `expected_impact`, `target_files` — all REQUIRED |
| `ExperimentStore` | `store` | Manages `.factory/` directory; all mutating operations MUST use `FileLock` at `.factory/.store.lock` |
| `Workflow` | `workflow.primitives` | Named DAG of typed nodes; `validate_graph()` MUST return empty list for valid graphs |
| `AgentRunRequest` / `AgentRunResult` | `models` | Structured I/O for all runner invocations |

[[graph:factory.models]]

### 7.2  Supporting Entities

| Entity | Module | Purpose |
|---|---|---|
| `FEECCategory` | `strategy` | `IntEnum`: FIX=0, EXPLOIT=1, EXPLORE=2, COMBINE=3 (lower = higher priority) |
| `AdversarialState` | `models` | GAN-style loop: `active_role` alternates generator/discriminator; `converged` flag MUST be sticky once set |
| `CycleState` | `models` | In-flight cycle identity at `.factory/state/cycle.json`; mode MUST be immutable across respawns |
| `CheckpointState` | `checkpoint` | Crash-recovery snapshot at `.factory/checkpoint.json`; backward-compatible with missing fields |
| `RefinementState` | `refine_state` | Post-cycle refinement tracking at `.factory/state/refinements.json` |
| `ProjectRegistry` | `registry` | Global registry at `~/.factory/registry.json`; self-registration on `ExperimentStore.begin()` |
| `PlaybookItem` / `Playbook` | `ace` | Per-agent behavioral rules with `helpful`/`harmful` counters; `net_score = helpful − harmful` |
| `ResearchTarget` | `models` | Research mode objective with `run_command`, `result_path`, `timeout` (default 3600s) |
| `ReviewPayload` | `review` | Structured PR review: verdict, scores, guards, precheck summary, QA body |
| `LeakageReport` | `research.leakage` | `risk_level` ∈ {`none`, `medium`, `high`}; `flagged: bool` |

[[graph:factory.models]]
[[graph:factory.strategy]]
[[graph:factory.ace]]

### 7.3  Entity Relationships

```
FactoryConfig ─1:1─→ ExperimentStore (parsed from factory.md, persisted as config.json)
ExperimentStore ─1:*─→ ExperimentRecord (append-only results.tsv)
ExperimentStore ─1:1─→ EvalProfile (eval_profile.json)
ExperimentStore ─1:1─→ ProjectRegistry (self-registers on begin())
FactoryConfig ─0:1─→ ResearchTarget (research mode only)
FactoryConfig ─0:1─→ AdversarialConfig (adversarial mode only)
FactoryConfig ─0:*─→ ProjectEvalDimension (user-defined eval dimensions)
FactoryConfig ─0:*─→ HardConstraint (non-overridable quality gates)
Workflow ─*:*─→ NodeType (AgentNode | FnNode | GateNode | ForkNode | JoinNode | Study)
Workflow ─*:*─→ Edge (directed, optionally conditioned on VerdictType)
Factory ─1:*─→ Workflow (select_workflow via trigger matching)
Factory ─1:*─→ AgentConfig (DEFAULT_AGENT_POOL)
```

[[graph:path:FactoryConfig:ExperimentStore]]
[[graph:path:Workflow:AgentNode]]

---

## 8  State Machines

### 8.1  Project State Detection

The factory MUST detect project state via `detect_state()` using this priority-ordered evaluation. Higher-priority checks MUST short-circuit lower ones.

```
┌──────────────────────────────────────────────────────────────────────┐
│  1. Path missing OR no .git/         →  NO_REPO                     │
│  2. eval_profile.json exists with    →  EVALS_PENDING_REVIEW        │
│     human_reviewed ≠ true                                           │
│  3. .factory/config.json exists      →  HAS_FACTORY                 │
│  4. Open GitHub issues with          →  REPO_INCOMPLETE             │
│     label="plan" (via gh CLI)                                       │
│  5. Otherwise                        →  NO_FACTORY                  │
└──────────────────────────────────────────────────────────────────────┘
```

**Constraints:**
- `implementation` label issues MUST NOT trigger `REPO_INCOMPLETE` — that is the factory's own backlog label.
- `_has_open_plan_issues` MUST return `False` on `FileNotFoundError`, `TimeoutExpired`, empty response, or non-zero returncode.
- Malformed `eval_profile.json` (invalid JSON, missing `human_reviewed` key) MUST be treated as pending review.
- `.factory/` directory without `config.json` SHOULD log a structured warning.

[[graph:factory.state]]

### 8.2  CEO Mode Resolution

```
INPUT ──→ _resolve_input ──→ (project_path, context)

Mode detection (_auto_detect_mode):
  In-flight CycleState exists?
    YES → return cycle mode (prevents mode flip on respawn)
    NO  →
      NO_REPO | REPO_INCOMPLETE  → "build"
      NO_FACTORY + has_prompt    → "build"
      NO_FACTORY + no_prompt     → "discover"
      EVALS_PENDING_REVIEW       → "discover"
      HAS_FACTORY + research_target → "research"
      HAS_FACTORY                → "improve"

Mode chaining (_chain_modes):
  Build → Discover → Review → Improve (auto-chain, max 3 hops)
  Terminal workflows (terminal=True) → stop chaining immediately
```

**Constraints:**
- `--design` + `--headless` → MUST exit 1 (incompatible).
- `--focus` + `--loop` → MUST exit 1 (mutually exclusive).
- `--refine` + (`--mode` | `--prompt` | `--focus`) → MUST exit 1 (mutually exclusive).
- `--mode review/qa/deep-qa` without `--pr` → MUST exit 1.
- Stale `CycleState` (>24 hours) MUST be ignored.

[[graph:factory.cli.ceo]]
[[graph:factory.ceo_completion]]

### 8.3  Experiment Lifecycle

```
           ┌─────────────────────────────────────────────────┐
           │                                                 │
  begin() ─→ ACTIVE ─→ save_eval("before") ─→ [builder] ─→ │
           │             save_eval("after")  ─→ save_diff() │
           │                                                 │
           └─→ finalize(record) ─→ FINALIZED                │
                  │                     │                    │
                  │  verdict="keep"     │  verdict="revert"  │
                  │  + precheck pass    │  OR precheck fail  │
                  ▼                     ▼                    │
              KEPT                   REVERTED                │
                                                             │
                  verdict="error" ───────→ ERRORED           │
                                                             │
           └─────────────────────────────────────────────────┘
```

**Invariants:**
- `begin()` MUST use `FileLock` for concurrent ID allocation; IDs MUST be sequential starting at 1.
- `begin()` MUST NOT overwrite an existing `hypothesis.md`.
- `begin()` MUST register the project in the global registry (non-blocking, failure tolerated).
- `finalize()` MUST auto-compute `delta` as `score_after − score_before` when both are non-None and `delta` is None.
- `finalize()` MUST use `FileLock` for concurrent TSV append.
- `finalize()` MUST auto-create the experiment directory if it was deleted.
- `cmd_finalize` with `verdict="keep"` + failing precheck → MUST override to `"revert"` with `"OVERRIDDEN"` note.
- `cmd_finalize` with `verdict="revert"` → MUST skip precheck entirely.
- `cmd_finalize` with `force=True` → MUST bypass precheck.

[[graph:factory.store]]

### 8.4  Workflow Execution

```
START_NODE ──→ [dispatch by type]

  AgentNode/FnNode/Study:
    blocking=True  → execute → follow unconditional edge
    blocking=False → fire as background task → follow edge immediately

  ForkNode:
    dispatch all targets concurrently via asyncio.gather
    → follow fork's unconditional edge (excluding branch targets)

  JoinNode:
    pass-through (no execution) → follow unconditional edge

  GateNode:
    evaluate → Verdict
    PROCEED → follow PROCEED edge (or unconditional fallback)
    RELOOP  → check iteration_count < max_iterations
              → accumulate feedback in node_context
              → re-execute target node
    HALT    → set halted=True, stop workflow
```

**Invariants:**
- Background tasks MUST be awaited with 30s timeout at workflow end; pending tasks MUST be cancelled.
- `_wait_for_reads` MUST poll `completed_files` with 0.1s interval, 60s timeout. Timeout MUST cause halt.
- Reloop exceeding `max_iterations` (default 3) MUST halt the workflow.
- Agent nodes that exit non-zero MUST raise `RuntimeError`.
- Dry-run mode: all nodes MUST return `"[dry-run] {id} executed"`; all gates MUST return PROCEED.
- User gates in non-dry-run mode MUST auto-PROCEED (no interactive prompt in headless).
- Fn gate verdict parsing: JSON `{"passed": true}` → PROCEED; `{"passed": false}` → HALT. Line-prefix fallback: `pass:` → PROCEED, `fail:`/`revert:` → HALT, `reloop:` → RELOOP with feedback.

[[graph:factory.workflow.executor]]

### 8.5  Adversarial Eval Loop

```
Start: active_role="generator", current_round=0

For each record_phase_result(score):
  1. Increment current_round
  2. If score ≥ threshold:
       increment consecutive_above
       increment per-role counter (generator_ or discriminator_consecutive_above)
     Else:
       reset consecutive_above to 0
       reset per-role counter to 0
  3. If consecutive_above ≥ hysteresis:
       switch active_role (generator ↔ discriminator)
       reset consecutive_above to 0
       (preserve per-role counters)
  4. If BOTH per-role counters ≥ convergence_window:
       set converged = True (sticky — MUST NOT be unset)
```

[[graph:factory.adversarial]]

### 8.6  CEO Completion Guard

```
FRESH_CYCLE → RUNNING → [COMPLETE | INCOMPLETE]
  INCOMPLETE → RESPAWN (increment respawns) → RUNNING
  INCOMPLETE → CAP_HIT (write cycle-incomplete.md, exit 1)
  RUNNING + SIGINT (code 130) → EXIT (no respawn)
  RUNNING + cycle.aborted event → EXIT (no respawn)
  COMPLETE → DELETE_CYCLE_STATE → EXIT(0)
```

**Constraints:**
- Max respawns: default 5, env-overridable via `FACTORY_CEO_MAX_RESPAWNS`.
- `FACTORY_CEO_RESPAWN_DISABLED=1` MUST disable the entire guard.
- `background=True` MUST bypass the respawn loop entirely.
- Continuation tasks MUST include mode override header with `cycle_id` to prevent mode flipping.

[[graph:factory.ceo_completion]]

### 8.7  ACE Curator (3-Phase Pruning)

```
Phase 1: Merge candidates — deduplicate at similarity ≥ 0.75, merge counters
Phase 2: Remove net-negative items — (harmful > helpful with ≥3 observations)
         OR (harmful − helpful ≥ 3)
Phase 3: Cap at max_items=15 by net_score → reassign sequential IDs
```

[[graph:factory.ace.curator]]

---

## 9  Shared Contracts

### 9.1  Eval Runner — Score Computation

The eval runner MUST compute scores in this order:
1. **Hygiene dimensions** (6 mandatory) — auto-detect project tooling via language evaluators
2. **Project eval** (`eval/score.py`) — optional additive dimensions (MUST NOT override mandatory)
3. **Growth dimensions** (6 mandatory) — capability surface, experiment diversity, observability, research grounding, factory effectiveness, spec compliance
4. **Custom project eval** (from `factory.md ## Project Eval`) — user-defined dimensions
5. **Auto-promoted eval_spec** — executable items promoted to `ProjectEvalDimension`
6. **Merge all** with weight normalization

**Weight distribution:**
- No project eval: 50% hygiene + 50% growth
- With project eval, explicit weights: normalize to sum 1.0
- With project eval, default weights: auto-distribute to 30% hygiene + 20% growth + 50% project

**`_normalize_tier` contract:**
- Within-tier weight overrides MUST be applied before normalization.
- Weights MUST sum to `target_weight` after normalization.
- Empty results or zero target → MUST return `[]`.
- Scores, details, and `passed` flags MUST be preserved through normalization.

[[graph:factory.eval.runner]]
[[graph:factory.eval.scorer]]

### 9.2  Strategy — FEEC Priority Heuristic

**`categorize_hypothesis(text)` contract:**
- MUST check keywords in priority order: FIX → EXPLOIT → COMBINE → EXPLORE
- First match wins; no match → EXPLORE
- Case-insensitive matching
- FIX keywords: `fix`, `error`, `bug`, `crash`, `fail`, `regression`, `broken`, `repair`
- EXPLOIT keywords: `improve`, `increase`, `extend`, `enhance`, `build on`, `optimize`, `boost`
- COMBINE keywords: `combine`, `merge`, `integrate`, `unify`, `consolidate`

**`detect_stuck(history, threshold)` contract:**
- MUST return `True` only when the last `threshold` consecutive reverts share the same FEEC category.
- A `keep` verdict MUST reset the streak.
- Fewer than `threshold` entries → MUST return `False`.

**`detect_plateau(history, threshold)` contract:**
- MUST compare running-best `score_after` across scored experiments.
- Experiments without `score_after` MUST be skipped (not counted toward streak).
- Fewer than `threshold` scored experiments → MUST return `False`.

[[graph:factory.strategy]]

### 9.3  Store — Experiment Filesystem Store

**`init(config)` contract:**
- MUST create `.factory/`, `experiments/`, `strategy/`, `agents/`, `reviews/` directories.
- MUST write `config.json` from config model.
- MUST create `results.tsv` with column headers if not present.
- MUST handle broken symlinks at `.factory/` path (remove and recreate).
- MUST be idempotent.

**`reparse_config()` contract:**
- MUST parse `factory.md` markdown sections by heading level.
- MUST handle code fences (content inside treated as scalar value).
- Section name mapping: `command` → `eval_command`, `threshold` → `eval_threshold`, `modifiable` → `scope`, `multi-run`/`multi_run` → `inner_loop`, `surface_scoping` → `outer_loop_surfaces`.
- `test_timeout` MUST clamp to minimum 1 (negative/zero → 600).

**`load_history()` contract:**
- Invalid verdict values MUST be coerced to `"error"`.
- `""`, `"-"`, `"n/a"` MUST parse as `None` for numeric fields.

[[graph:factory.store]]

### 9.4  Precheck — Non-Overridable Quality Gate

The CEO CANNOT override precheck failures — a single failure MUST cause mandatory revert.

**Checks (6):**
1. `check_score_direction` — score MUST NOT regress AND MUST meet threshold
2. `check_scope` — changed files MUST match scope glob patterns
3. `check_surfaces` — fixed surfaces MUST NOT be modified
4. `check_anti_pattern` — hypothesis MUST NOT match reverted experiments above similarity threshold
5. `check_hard_constraints` — all user-defined shell commands MUST exit 0
6. `check_qa_execution` — QA agent MUST have been invoked (Sacred Rule 9); matches both monolithic QA and deep-QA specialist events

**`run_precheck()` contract:**
- MUST run all applicable checks and aggregate results.
- A single check failure MUST cause `passed=False`.
- MUST report `blocking_failures` list.

[[graph:factory.precheck]]

### 9.5  Agent Runner — Agent Invocation

**`resolve_prompt(role)` contract:**
- Resolution order: project override (`.factory/agents/<role>.md`) > factory default.
- Playbook injection MUST follow the prompt, before user profile.
- CEO role with `workflow_mode`: MUST inject workflow playbook from `skills/workflow-*/SKILL.md`.
- Non-CEO roles MUST ignore `workflow_mode`.
- Missing skill file MUST NOT cause an error.

**`invoke_agent()` contract:**
- MUST track consecutive failures via global counter.
- 2 consecutive failures → MUST raise `ConsecutiveAgentFailureError`.
- Success MUST reset the failure counter.
- MUST emit `agent.started`, `agent.completed`/`agent.failed`/`agent.timeout` events.
- MUST save review output to `.factory/reviews/<role>-latest.md`.

[[graph:factory.agents.runner]]

### 9.6  Runner Abstraction

**Runner selection (`get_runner`):**
- Resolution: explicit name → `FACTORY_RUNNER` env → config.toml → `"claude"` default.
- Unknown name → MUST raise `ValueError`.
- Four registered runners: `claude`, `bob`, `codex`, `opencode`.

**ClaudeRunner:**
- MUST include `--disallowedTools Agent` in all execution paths.
- MUST include `--output-format stream-json` and `--verbose` in headless mode.
- MUST strip `VIRTUAL_ENV` from subprocess environment.

**BobRunner:**
- MUST enforce invocation ceilings per cycle (default 8, env-overridable).
- `CeilingExceededError` at limit; `CeilingWarning` when ≤2 remaining.
- MUST log all invocations to `.factory/bob_usage.jsonl`.

**CodexRunner:**
- MUST isolate OAuth and API key auth modes (OAuth strips API key from env).
- MUST use `codex exec --sandbox workspace-write` for headless.

[[graph:factory.runners]]

### 9.7  Worktree Isolation

**`create_worktree()` contract:**
- MUST resolve `base_branch` to commit SHA before creating (handles amended HEAD).
- Directory: `project/.factory-worktrees/run-<id>`.
- Branch: `factory/run-<id>`.
- MUST symlink `.factory/` from worktree to main project (shared state).
- MUST emit `worktree.created` event.

**`remove_worktree()` contract:**
- MUST be idempotent (safe to call twice).
- MUST delete both directory and branch.
- MUST emit `worktree.removed` event.

**Concurrency invariant:**
- `ExperimentStore.begin()` across worktrees MUST produce unique sequential IDs (ensured by shared `FileLock` via `.factory` symlink).

[[graph:factory.worktree]]

---

## 10  Entry Points

| Command | Purpose | Key Flags |
|---|---|---|
| `factory ceo <path>` | Launch CEO agent (interactive by default) | `--mode`, `--focus`, `--headless`, `--refine`, `--loop` |
| `factory run <path>` | Run factory cycle (delegates to CEO) | `--loop`, `--interval`, `--max-cycles` |
| `factory tmux <path>` | Launch in detached tmux session | `--attach`, `--session` |
| `factory agent <role>` | Invoke specialist agent directly | `--task`, `--project`, `--runner` |
| `factory eval <path>` | Run project evals, print JSON | `--skip-project-eval` |
| `factory begin <path>` | Start experiment, print ID | `--hypothesis` |
| `factory finalize <path>` | Finalize experiment with verdict | `--verdict`, `--force` |
| `factory precheck <path>` | Run hard precheck gate | `--score-before`, `--score-after` |
| `factory spec generate <path>` | Generate repo spec | — |
| `factory workflow run <name>` | Execute workflow graph headlessly | `--project`, `--dry-run` |
| `factory dashboard` | Launch live web dashboard | `--port`, `--projects-dir` |
| `factory refactory` | Launch persistent supervisor agent | `--reset` |

When invoked without a subcommand in a TTY, the CLI MUST default to `refactory` mode.

[[graph:factory.cli]]

---

## 11  Eval System

### 11.1  Dimension Taxonomy

**Hygiene dimensions (6 mandatory, sum to 1.0 within tier):**

| Dimension | Detection | Scoring |
|---|---|---|
| `tests` | pytest, jest/vitest, go test, cargo test | Pass/fail + partial credit |
| `lint` | ruff, eslint, go vet, cargo clippy | `max(0.0, 1.0 − violations × 0.05)` |
| `type_check` | mypy, tsc, go build, cargo check | Pass/fail |
| `coverage` | coverage.py, jest --coverage, go test -cover, tarpaulin | Percentage-based |
| `config_parser` | Config file validation | Pass/fail |
| `architecture` | sentrux scan (requires `.sentrux/rules.toml`) | 0.5 neutral when missing |

**Growth dimensions (6 mandatory, sum to 1.0 within tier):**

| Dimension | Scoring |
|---|---|
| `capability_surface` | Module and public function count, scaled to project size |
| `experiment_diversity` | 0.5 neutral for <3 experiments; penalizes repeated hypotheses |
| `observability` | 0.40×coverage + 0.25×structured + 0.20×tracing + 0.15×density |
| `research_grounding` | Archive sources or vault fallback; `doc_ratio` from experiment notes |
| `factory_effectiveness` | Keep rate across managed projects; 0.5 neutral for <2 experiments |
| `spec_compliance` | From `.factory/spec_results.json`; stale (>24h) → 0.5 |

[[graph:factory.eval.hygiene]]
[[graph:factory.eval.growth]]

### 11.2  Language Evaluator Registry

Evaluators MUST be registered at import time. `detect_languages()` returns all matching evaluators (multi-language projects supported).

| Evaluator | Language | Test | Lint | Type Check | Coverage |
|---|---|---|---|---|---|
| `PythonEvaluator` | Python | pytest | ruff | mypy | coverage.py |
| `NodeEvaluator` | TypeScript/JS | jest/vitest | eslint | tsc | jest --coverage |
| `GoEvaluator` | Go | go test | go vet | go build | go test -cover |
| `RustEvaluator` | Rust | cargo test | cargo clippy | cargo check | tarpaulin |

**`EvalFragment` invariant:** Scores MUST be clamped to [0.0, 1.0].

[[graph:factory.eval.languages]]

### 11.3  Guard System

`check_all()` MUST run all applicable guards and return a flat list of violation strings (empty ≡ pass).

| Guard | Check |
|---|---|
| `check_git_clean` | Working tree clean (lock files excluded) |
| `check_eval_immutable` | Eval tree unchanged since snapshot |
| `check_experiment_branch` | At least one commit after baseline |
| `check_scope` | Changed files match scope glob patterns |
| `check_fixed_surfaces` | Fixed surface globs not modified (lock files excluded) |

**`_glob_match()` MUST support:** exact match, `**` recursive matching, `*` single-level wildcard, prefix matching.

[[graph:factory.eval.guards]]

---

## 12  Workflow Definitions

### 12.1  Registered Workflows

**Core:** build, design, improve, qa, research, meta, discover, review, refine, create, skill-refine, doc-generate, doc-update, spec-generate, spec-update.

**Contributed benchmarks:** swebench, featurebench, legacybench, terminalbench, programbench.

All workflows MUST pass `validate_graph()` — structural DAG invariant.

### 12.2  Workflow Composition Rules

- `design_workflow()` MUST mutate `build_workflow()` — same node set, `gate_strategy.evaluator_type` changed to `"user"`.
- `research_workflow()` MUST mutate `improve_workflow()` — replaces `study` with `baseline`, adds `failure_analyst` and `plateau_gate`.
- `meta_workflow()` MUST extend `improve_workflow()` with `insights` FnNode and test pruning pipeline.

### 12.3  Deep-QA Subgraph

Shared by build, improve, research, refine, and create workflows:
- Nodes: `health_checker` → `code_reviewer` → `gate_review` (fn, checks `CRITICAL_FOUND`) → `adversarial_tester`
- `gate_doc_freshness`: CEO agent gate between `gate_qa` and `gate_precheck`; RELOOP → builder.

**Builder→QA reachability:** MUST be enforced for all non-benchmark workflows.

### 12.4  Trigger Conditions

| Workflow | State | Additional |
|---|---|---|
| build | `NO_REPO` or `REPO_INCOMPLETE` | — |
| design | `NO_REPO` | `interactive=True` |
| improve | `HAS_FACTORY` | — |
| research | `HAS_FACTORY` | `research_target` present |
| meta | `HAS_FACTORY` | `mode="meta"` |
| create | any | `mode="create"` |

### 12.5  Benchmark Workflows

All 5 contributed benchmark workflows share:
- **Pipeline:** study → builder → gate_verify → auto_merge (programbench adds `reviewer`)
- **Terminal:** `terminal=True` — no factory infrastructure (no eval, experiments, or deep-QA)
- **RELOOP:** gate_verify → builder on test failure, max 3 iterations
- **Auto-merge:** Updates main branch ref via `git update-ref`, copies changed files

| Benchmark | Domain | Gate Logic |
|---|---|---|
| swebench | Python bug fixes | Builder self-report (pass/fail keywords) |
| featurebench | Python features | Builder self-report |
| legacybench | COBOL/Fortran/C/Java7/Assembly | `make test` execution |
| terminalbench | Terminal tasks | Broad keyword matching |
| programbench | Binary reverse engineering | Structured `todos.md` + compile.sh + multi-tier test |

[[graph:factory.workflow.definitions]]

---

## 13  Failure Model

### 13.1  Error Types

| Error | Module | Trigger | Recovery |
|---|---|---|---|
| `ConsecutiveAgentFailureError` | `agents.runner` | 2+ consecutive spawn failures | Emits `cycle.aborted`; CEO completion guard stops respawning |
| `CeilingExceededError` | `runners.usage` | Bob invocations exceed per-cycle limit | Actionable message with env var to bump |
| `ResultParseError` | `models` | Result file unparseable | `RunStatus.ERROR`, metric_value=0.0 |
| `FileNotFoundError` | `store.read_config` | `.factory/config.json` missing | Message: "Run 'factory init' first" |
| `ValueError` | `store.read_config` | Invalid JSON or failed validation | Message: "Run 'factory init --reparse' to regenerate" |
| `ValidationError` | `models` | Extra fields on strict Pydantic models | Pydantic error detail with field name |

### 13.2  Graceful Degradation

The following modules MUST degrade gracefully (return empty/no-op) on external failures:

| Module | Degradation |
|---|---|
| `study.py` | All external ops return empty results, MUST NOT raise |
| `telemetry.py` | All Langfuse operations wrapped in try/except with `log.debug` |
| `registry.py` | Missing/corrupt registry file → empty registry |
| `obsidian/notes.py` | All write functions return `None` when vault unconfigured |
| `notify/telegram.py` | POST failure logged but MUST NOT be raised |
| `checkpoint.py` | Corrupt/invalid-schema files → return `None` |
| `skill_cache.py` | Any I/O error → return empty list |
| `state.py` | `_has_open_plan_issues` returns `False` on timeout, missing `gh`, or any subprocess error |

### 13.3  Concurrency Safety

| Operation | Mechanism |
|---|---|
| Experiment ID allocation | `FileLock` at `.factory/.store.lock` |
| TSV append | `FileLock` at `.factory/.store.lock` |
| Registry writes | Atomic tmp-file + rename pattern |
| Config file creation | `O_EXCL` (atomic create-or-skip) with `0o600` permissions |
| Worktree creation | Unique branch names via `run_id` |
| Background agent nodes | `asyncio.Task` with 30s timeout on workflow exit |

---

## 14  Security Constraints

- Sensitive keys in config.toml MUST be masked (show last 4 chars or `****`).
- Bob auth file (`.factory/.bob_auth`) MUST have `0o600` permissions.
- Config.toml MUST be created with `0o600` permissions via `O_EXCL`.
- Message queue MUST block path traversal via `is_relative_to()` check.
- Message queue MUST skip symlinks in the message directory.
- Dashboard MUST validate all path segments against `_SAFE_NAME_RE`.
- `VIRTUAL_ENV` MUST be stripped from all runner subprocess environments.
- Agent prompts MUST include `--disallowedTools Agent` (prevents native Agent tool).
- Ground truth leakage detection MUST run before research hypotheses are accepted.

---

## 15  External Integrations

### 15.1  Issue Tracking (`factory.issue`)

`parse_issue_ref()` MUST handle: bare numbers, GitHub/GitLab URLs, `owner/repo#N` shorthand.
`fetch_issue()` MUST shell out to `gh`/`glab` CLI — MUST NOT call APIs directly.

[[graph:factory.issue]]

### 15.2  Obsidian Vault (`factory.obsidian`)

All vault operations MUST be gated on `FACTORY_VAULT_PATH` env var. Legacy `OBSIDIAN_VAULT_PATH` MUST be ignored.

[[graph:factory.obsidian]]

### 15.3  Langfuse Telemetry (`factory.telemetry`)

Enabling requires: `langfuse` package installed AND (`LANGFUSE_HOST` or `LANGFUSE_BASE_URL`) env var set.
TranscriptTailer MUST run as daemon thread, polling JSONL transcripts at 5s interval.

[[graph:factory.telemetry]]

### 15.4  Telegram Notifications (`factory.notify.telegram`)

Implements `Notifier` protocol. MUST silently skip when `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` are missing.

[[graph:factory.notify.telegram]]

### 15.5  MCP Server (`factory.mcp_server`)

Exposes 4 tools: `factory_get_score`, `factory_list_experiments`, `factory_get_status`, `factory_list_projects`. All MUST return JSON strings. Error cases MUST return `{"error": "..."}`.

[[graph:factory.mcp_server]]

### 15.6  Dashboard (`factory.dashboard`)

FastAPI server on port 8420 with SSE event streaming. Path-segment parameters MUST be validated against `_SAFE_NAME_RE` to prevent directory traversal.

[[graph:factory.dashboard]]

---

## 16  Extension Points

### 16.1  Contributed Workflows

New benchmark or domain-specific workflows MAY be added to `factory/workflow/contributed/`. Each contributed workflow MUST:
1. Define a `Workflow` instance with a trigger function.
2. Register itself in `register_all()`.
3. Include a `test_workflow.py` validating registration, meta fields, and terminal status.
4. Set `terminal=True` if it does not use factory eval/experiment infrastructure.

[[graph:factory.workflow.definitions]]

### 16.2  Language Evaluators

New language evaluators MAY be added to `factory/eval/languages/`. Each evaluator MUST:
1. Implement the evaluator protocol: `detect()`, `run_tests()`, `run_lint()`, `run_type_check()`, `run_coverage()`.
2. Register via `register_evaluator()` at import time.
3. Return `EvalFragment` values with scores clamped to [0.0, 1.0].

[[graph:factory.eval.languages]]

### 16.3  Runner Backends

New runner backends MAY be added to `factory/runners/`. Each runner MUST:
1. Implement the `Runner` protocol: `headless()`, `interactive_run()`, `build_command()`, `metadata()`.
2. Register via `register_runner()`.
3. Provide dry-run support via an environment variable flag.

[[graph:factory.runners]]

### 16.4  Agent Prompt Overrides

Per-project agent behavior MAY be customized by placing `<role>.md` files in `.factory/agents/`. These override the factory default prompts for the corresponding role.

---

## 17  Configuration

### 17.1  Five-Tier Precedence

```
CLI flag > env var > profile credential > config.toml default > hardcoded default
```

Profile credentials collapse into the env tier via `os.environ.setdefault` — env vars always win.

### 17.2  `~/.factory/config.toml` Structure

```toml
[defaults]
runner = "claude"          # Runner backend
model = ""                 # Model override
projects_dir = "~/factory-projects"

[credentials.vertex]       # Named credential profile
FACTORY_RUNNER = "claude"
ANTHROPIC_API_KEY = "sk-ant-..."
```

**Validation:**
- Profile names MUST match `[a-zA-Z0-9_-]+`.
- Credential keys MUST match `[A-Z_][A-Z0-9_]*`.
- Sensitive keys (containing `key`, `token`, `secret`, `password`) MUST be masked in display.
- Config file MUST be created with `0o600` permissions.

[[graph:factory.user_config]]

---

## 18  Event System

All events MUST be appended to `.factory/events.jsonl` in JSONL format. The file MUST resolve symlinks before writing.

### 18.1  Event Types

**Agent lifecycle:** `agent.started`, `agent.completed`, `agent.failed`, `agent.timeout`
**Experiment lifecycle:** `experiment.begin`, `experiment.finalize`
**Workflow lifecycle:** `workflow.started`, `node.started`, `node.completed`, `node.failed`, `gate.verdict`, `workflow.completed`, `workflow.halted`
**Other:** `cycle.started`, `cycle.completed`, `worktree.created`, `worktree.removed`, `backlog.added`, `backlog.removed`, `verdict.overridden`, `ceo.message`, `bob.ceiling_warning`, `bob.ceiling_exceeded`

### 18.2  Safety

`_emit_cli_event` MUST swallow exceptions (MUST NOT crash the CLI).
`load_events` MUST support `since` datetime filter and skip blank lines.

[[graph:factory.events]]

---

## 19  Cross-Project Intelligence

### 19.1  Insights Engine

`classify_hypothesis()` uses 13 categories in priority order: bugfix > observability > coverage > testing > lint > type_safety > refactoring > performance > eval_improvement > agent_improvement > prompt_engineering > infrastructure > feature.

Projects with <3 experiments MUST be excluded from category stats.

[[graph:factory.insights]]

### 19.2  ACE — Autonomous Continuous Evolution

**Reflector contract:**
- MUST be deterministic (no LLM) — pattern extraction from experiment histories only.
- Counter wiring: fuzzy-matches hypothesis text against bullet content (term overlap ≥0.4 or SequenceMatcher ≥0.35).
- keep → `helpful+1`; revert → `harmful+1`; error → no change.

**Injector contract:**
- Two-tier playbook resolution: user-local (`~/.factory/playbooks/`) > factory defaults.
- `seed_user_playbooks()` copies defaults but MUST NOT overwrite existing files.

[[graph:factory.ace]]

---

## 20  Discovery Pipeline

```
introspect.py → ProjectProfile
     │  (language, type, framework, tooling detection)
     ▼
profile.py → EvalProfile
     │  (3-tier dimension resolution: discovered > researched > fallback)
     │  (weight normalization to sum 1.0)
     ▼
generate.py → eval/score.py
     │  (templated Python wrapping discovered tools)
     ▼
eval_spec.py → starter eval_spec items
     │  (auto-promotion: executable → ProjectEvalDimension)
     ▼
spec.py → SPEC.md
     (graphify-first with batched fallback)
```

**Tier confidence:** explicit=1.0, discovered=0.8, researched=0.5, fallback=0.2.

[[graph:factory.discovery]]

---

## 21  Specification Operations

**`generate_spec()` pipeline (two paths):**
1. **Graph path (preferred):** `extract_graph()` → `load_graph_data()` → single agent call with graph summary → write `SPEC.md`
2. **Batched fallback:** collect source files → group into batches (80K token limit) → parallel agent extraction → concatenate to `spec_raw.md` → annotation agent → write `SPEC.md`

**`validate_spec()` MUST include graph reference validation** — `[[graph:...]]` entity/community refs MUST resolve to actual graph nodes.

**`get_impact()` MUST prefer graph-based analysis** (instant NetworkX traversal) with agent fallback.

[[graph:factory.spec]]

---

## Appendix A  `.factory/` File Layout

```
.factory/
├── config.json               # FactoryConfig (from factory.md)
├── eval_profile.json          # EvalProfile (from discover)
├── results.tsv                # Append-only experiment history
├── performance_report.json    # Consolidated for ACE
├── checkpoint.json            # Crash-recovery state
├── adversarial_state.json     # GAN-style loop state
├── last_eval.json             # Latest eval for dashboard
├── events.jsonl               # Append-only event log
├── bob_usage.jsonl            # Bob invocation log
├── citations.json             # Research citation backfill
├── .store.lock                # FileLock for concurrent access
├── experiments/
│   └── NNN/                   # hypothesis.md, eval_*.json, changes.diff, verdict.json
├── strategy/
│   ├── current.md             # Active hypotheses
│   ├── backlog.md             # Pending items
│   ├── observations.md        # Study output
│   ├── research.md            # Researcher output
│   └── insights.md            # Cross-project insights
├── reviews/
│   ├── <role>-latest.md       # Agent output capture
│   └── ceo-verdict-<role>.md  # CEO review verdicts
├── research/
│   └── runs/<cycle_id>/       # stdout.log, stderr.log, summary.json
├── state/
│   ├── cycle.json             # CycleState
│   └── refinements.json       # RefinementState
├── archive/
│   ├── experiments/           # Per-experiment learnings
│   ├── patterns/              # Recurring patterns
│   ├── decisions/             # Architecture decisions
│   └── sources/               # Research source notes
├── agents/                    # Per-project agent prompt overrides
├── messages/                  # User→CEO message queue
│   └── read/                  # Consumed messages
├── spec_raw.md                # Raw spec extraction (batched path)
├── spec_validation.md         # Spec validation report
├── spec_update_scope.md       # Spec update scope
└── graphify-out/
    └── graph.json             # Code knowledge graph
```

---

## Appendix B  Implementation Checklist

- [ ] All Pydantic models use `ConfigDict(strict=True, extra="forbid")`
- [ ] All mutating store operations acquire `FileLock`
- [ ] All workflows pass `validate_graph()`
- [ ] Builder→QA reachability enforced for non-benchmark workflows
- [ ] All language evaluators register at import time
- [ ] All runners provide dry-run support
- [ ] Event emission never crashes the host process
- [ ] Precheck failures always cause mandatory revert
- [ ] Config files created with `0o600` permissions
- [ ] Dashboard validates path segments against `_SAFE_NAME_RE`
- [ ] `VIRTUAL_ENV` stripped from all runner subprocess environments
- [ ] Graceful degradation modules never raise on external failure
- [ ] Contributed workflows set `terminal=True` and register in `register_all()`

---

## How to Read the Knowledge Graph

This spec uses `[[graph:...]]` reference links to point into a code knowledge graph extracted by graphify. The graph contains AST-derived entities (modules, classes, functions) and their typed relationships (imports, calls, inherits).

### Reference Link Types

- `[[graph:EntityName]]` — look up a specific entity (module, class, function)
- `[[graph:path:A:B]]` — find the dependency path between entities A and B
- `[[graph:query:question]]` — run a natural language query against the graph
- `[[graph:community:subsystem]]` — list all entities in a detected subsystem

### When to Use

- **Planning and design:** Read the overview sections in this spec
- **Implementation details:** Resolve `[[graph:...]]` links via `factory spec resolve` or query the graph directly with `graphify explain`, `graphify path`, `graphify query`
