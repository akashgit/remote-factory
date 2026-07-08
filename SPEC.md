# Behavioral Specification — Remote Factory

> **Revision:** 2026-07-07 · **Status:** Normative · **Notation:** [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119)

---

## §1 Problem Statement

Software projects accumulate technical debt, miss best practices, and stagnate without continuous, disciplined improvement. Human-driven improvement cycles are expensive, inconsistent, and bandwidth-limited.

The Remote Factory solves this by providing an **autonomous software improvement engine** — a four-layer system that detects a project's state, discovers evaluation dimensions, formulates improvement hypotheses, implements them via specialist agents, and verifies results through non-overridable quality gates. The system operates as a directed-graph workflow engine where each mode (build, improve, research, refine, etc.) is a typed DAG of agent nodes, function nodes, and gate nodes executed deterministically.

---

## §2 Goals and Non-Goals

### §2.1 Goals

1. Autonomously improve any software project through hypothesis-driven experiment cycles
2. Enforce non-overridable quality gates (precheck) that prevent regressions
3. Support multiple CLI backends (Claude Code, Bob Shell, Codex, OpenCode) via a runner abstraction
4. Evolve agent behavior over time through cross-project playbook learning (ACE)
5. Provide 20 workflow modes as composable, validated DAGs with formal execution semantics
6. Maintain full experiment history with append-only TSV and per-experiment artifact directories

### §2.2 Non-Goals

1. Direct API calls to LLM providers — the factory spawns CLI subprocesses exclusively
2. Real-time collaboration or multi-user concurrency on a single project
3. Replacement of human judgment on architectural decisions — the factory defers Tier 3 refinements

### §2.3 Design Philosophy

- **Hypothesis-driven**: Every change is an experiment with before/after eval, a verdict, and archival
- **Non-overridable gates**: The precheck gate cannot be bypassed by the CEO agent; failure means mandatory revert
- **Composable workflows**: Modes are DAGs built from 6 primitive node types, reusable via `subgraph()`
- **Self-improvement**: ACE pipeline evolves per-agent playbooks from cross-project experiment data
- **Fail-fast**: Consecutive agent failures (threshold=2) abort the cycle; corrupt state returns safe defaults
- **Deterministic orchestration, non-deterministic execution**: Workflow graphs define the DAG structure; agents produce non-deterministic output within those constraints
- **Five-tier configuration precedence**: CLI flag > env var > profile credential > config.toml > hardcoded default
- **Append-only history**: Experiment records in `results.tsv` are append-only; no retroactive modification

---

## §3 Project Identity

| Field | Value |
|---|---|
| Name | remote-factory |
| Language | Python 3.11+ |
| Type | CLI tool + agent orchestration engine |
| Package manager | uv |
| Entry point | `factory.cli:main` (registered as `factory` script) |
| Test runner | pytest (asyncio_mode=auto) |
| Linter | ruff (100-char line length) |
| Type checker | mypy |
| Logging | structlog (stderr, module-level `log = structlog.get_logger()`) |

---

## §4 Technical Stack

| Layer | Technology | Purpose |
|---|---|---|
| CLI framework | argparse (`_GroupedHelpParser`) | 70+ subcommands in 9 groups |
| Models | Pydantic v2 (strict, extra=forbid) | All domain types |
| Async runtime | asyncio | Workflow executor, eval runner, subprocess management |
| Concurrency | filelock (`FileLock`) | Safe concurrent experiment ID allocation and TSV append |
| Graph validation | networkx | Reachability, cycle detection, read/write consistency |
| Observability | Langfuse (optional, lazy init, graceful no-op) | Hierarchical span tracing with transcript ingestion |
| Dashboard | FastAPI/Starlette + SSE | Real-time project monitoring on port 8420 |
| Notifications | Telegram Bot API | Experiment digest delivery |
| Knowledge store | Obsidian vault (optional) | Experiment notes, project dashboards, strategy archives |
| Configuration | TOML (`~/.factory/config.toml`) | Five-tier precedence resolution |

---

## §5 Architecture Overview

The factory is a four-layer system:

### Layer 1: Python CLI (`factory/`)

Pure tools that do not make decisions. Entry point `factory/cli.py` dispatches via a handler dict to `cmd_*` functions organized in CLI module files (`cli/ceo.py`, `cli/admin.py`, `cli/store.py`, etc.). The CLI layer MUST NOT contain agent decision logic.

### Layer 2: Workflow Graph Engine (`factory/workflow/`)

All 20 factory modes are defined as directed graphs of typed nodes in `factory/workflow/definitions.py`. Each graph is a `Workflow` Pydantic model with `AgentNode`, `FnNode`, `GateNode`, `ForkNode`, `JoinNode`, and `Study` primitives connected by `Edge` objects.

The same graph definition produces two execution formats:
- **Headless**: `WorkflowExecutor` (`factory/workflow/executor.py`) walks the DAG deterministically
- **Interactive**: `skill_export.py` converts graphs to Claude Code `SKILL.md` files under `skills/workflow-*/`

### Layer 3: CEO Agent

The CEO prompt is split into core identity (`ceo.md`) and mode-specific playbooks (`skills/workflow-*/SKILL.md`). The CEO detects project state, reads the appropriate SKILL.md, and follows it as the mode-specific playbook.

### Layer 4: Specialist Agents (`factory/agents/`)

12 specialist roles spawned by the CEO via `factory agent <role>`. Agent prompts use a two-tier lookup: project override (`.factory/agents/<role>.md`) then factory default (`factory/agents/prompts/<role>.md`). ACE-evolved playbooks are auto-injected.

### Module Dependency Graph

```
factory/models.py                    ← Foundation: all Pydantic types
    ├── factory/state.py             ← 5-state project detection
    ├── factory/store.py             ← Experiment lifecycle (FileLock)
    ├── factory/eval/
    │   ├── runner.py                ← Mandatory dimensions + project eval merge
    │   ├── hygiene.py               ← 6 hygiene dimensions (multi-language)
    │   ├── growth.py                ← 6 growth dimensions
    │   ├── scorer.py                ← Weighted composite computation
    │   ├── guards.py                ← Git/scope/surface/immutability checks
    │   └── languages/{python,node,go,rust}.py  ← Per-language evaluators
    ├── factory/precheck.py          ← 6 non-overridable checks
    ├── factory/strategy.py          ← FEEC heuristic, plateau/stuck detection
    ├── factory/workflow/
    │   ├── primitives.py            ← 6 node types, Edge, Verdict, Workflow
    │   ├── definitions.py           ← 20 workflow DAGs
    │   ├── executor.py              ← Async DAG walker
    │   ├── validation.py            ← Graph validation (networkx)
    │   ├── skill_export.py          ← DAG → SKILL.md conversion
    │   ├── guard.py                 ← Slot/annotation integrity guard
    │   ├── splitter.py              ← Annotation extraction and slot resolution
    │   ├── templates.py             ← {{slot::default}} template variables
    │   └── registry.py              ← Workflow discovery (builtin/user/project)
    ├── factory/agents/
    │   ├── runner.py                ← Agent invocation + failure tracking
    │   └── prompts/*.md             ← Default agent prompt files
    ├── factory/ace/
    │   ├── reflector.py             ← Cross-project bullet generation
    │   ├── curator.py               ← 3-phase playbook pruning
    │   ├── injector.py              ← Playbook → prompt injection
    │   └── paths.py                 ← 2-tier path resolution
    ├── factory/runners/
    │   ├── protocol.py              ← Runner interface + RunnerMeta
    │   ├── claude.py                ← Claude Code backend (default)
    │   ├── bob.py                   ← Bob Shell backend + ceiling enforcement
    │   ├── codex.py                 ← OpenAI Codex backend
    │   ├── opencode.py              ← OpenCode backend
    │   ├── _subprocess.py           ← Shared subprocess execution
    │   ├── _stream.py               ← Stream processing, ANSI stripping, watchdog
    │   ├── _background.py           ← claude --bg background dispatch
    │   ├── _tmux_persist.py         ← Tmux window-based persistent sessions
    │   └── usage.py                 ← Bob-specific usage logging + ceiling
    ├── factory/research/
    │   ├── runner.py                ← Research run execution + result parsing
    │   └── leakage.py               ← Ground truth leakage detection
    ├── factory/spec/
    │   ├── generate.py              ← Batch extraction + annotation pipeline
    │   └── ops.py                   ← Validate, scope, update, impact operations
    ├── factory/ceo_completion.py     ← Completion guard + respawn logic
    ├── factory/registry.py           ← Global project registry (~/.factory/registry.json)
    ├── factory/user_config.py        ← Five-tier config resolution
    ├── factory/telemetry.py          ← Langfuse tracing (optional)
    ├── factory/skill_cache.py        ← SHA-256 checksum skill caching
    ├── factory/worktree.py           ← Git worktree lifecycle
    └── factory/clean_pr.py           ← PR artifact stripping
```

---

## §6 Domain Model

### §6.1 Core Enumerations

| Entity | Values | Description |
|---|---|---|
| **ProjectState** | `no_repo`, `incomplete`, `no_factory`, `evals_pending_review`, `has_factory` | Five-state project lifecycle |
| **VerdictType** | `proceed`, `reloop`, `halt` | Gate evaluation outcomes |
| **AgentRole** | `researcher`, `strategist`, `builder`, `qa`, `health_checker`, `code_reviewer`, `adversarial_tester`, `failure_analyst`, `ceo`, `archivist`, `refiner`, `skill_reviewer` | 12 specialist roles |
| **FEECCategory** | `FIX=0`, `EXPLOIT=1`, `EXPLORE=2`, `COMBINE=3` | Hypothesis priority (IntEnum; lower = higher priority) |
| **RunStatus** | `PASS`, `FAIL`, `ERROR`, `TIMEOUT` | Research run outcomes |
| **AggregateMethod** | `mean`, `median`, `max`, `all_pass` | Multi-run metric aggregation |

### §6.2 Configuration Models

All models use `ConfigDict(strict=True, extra="forbid")` — extra fields MUST raise `ValidationError`.

| Entity | Key Fields | Invariants |
|---|---|---|
| **FactoryConfig** | `goal`, `scope`, `guards`, `eval_command`, `eval_threshold`, `hypothesis_budget`, `research_target`, `mutable_surfaces`, `fixed_surfaces`, `hard_constraints`, `clean_pr`, `eval_spec`, `hygiene_weights`, `growth_weights` | `test_timeout` ≥ 1 (Field ge=1); `research_target` nullable; incomplete research target → `None` not error |
| **EvalProfile** | `project_type`, `dimensions[]`, `tier`, `confidence`, `human_reviewed` | `human_reviewed` defaults `false`; tier ∈ {explicit, discovered, researched, fallback}; weights MUST sum to 1.0 |
| **HypothesisBudget** | `min_growth`, `max_new` | Defaults: `min_growth=2`, `max_new=2` |
| **ResearchTarget** | `objective`, `metric`, `target`, `run_command`, `result_path`, `timeout` | `result_parser` MUST be `"json"`; all 4 required fields or `None` |
| **InnerLoopConfig** | `runs_per_cycle`, `aggregate`, `plateau_threshold` | `runs_per_cycle` ≥ 1; `aggregate` coerced from string via `@field_validator` |
| **HardConstraint** | `name`, `check`, `description` | Shell command; exit 0 = pass; non-zero = mandatory revert |
| **EvalWeights** | `hygiene`, `growth`, `project` | Defaults: 0.50, 0.50, 0.0; normalized to sum 1.0 |
| **TierWeights** | per-dimension weight overrides | Sparse — `None` fields keep defaults |

### §6.3 Experiment Models

| Entity | Key Fields | Invariants |
|---|---|---|
| **ExperimentRecord** | `id`, `timestamp`, `hypothesis`, `verdict`, `score_before`, `score_after`, `delta`, `cost_usd`, `research_citations` | `verdict` ∈ {keep, revert, error}; `delta` auto-computed on finalize; `research_citations` defaults to `[]` (backward compat) |
| **CompositeScore** | `total`, `results[]`, `guard_violations`, `passed` | `passed = (no guard_violations) ∧ (total ≥ threshold)` |
| **EvalResult** | `name`, `score`, `weight`, `passed`, `details` | Score clamped to [0.0, 1.0] at construction (via `EvalFragment`) |
| **CheckResult** | `name`, `passed`, `detail` | Dataclass — outcome of a single precheck |
| **PreCheckResult** | `passed`, `checks[]`, `blocking_failures[]` | Aggregate; `summary()` renders human-readable report |

### §6.4 Workflow Primitives

| Entity | Key Fields | Invariants |
|---|---|---|
| **Node** (base) | `id`, `reads`, `writes`, `blocking` | `blocking=True` by default; `reads`/`writes` are `set[str]` |
| **AgentNode** | `role`, `model`, `prompt_template`, `timeout`, `max_iterations` | Spawns a specialist agent |
| **FnNode** | `command`, `callable_name` | Runs a deterministic shell command |
| **GateNode** | `evaluator_type`, `evaluator_role`, `evaluator_command`, `gate_prompt` | `evaluator_type` ∈ {agent, fn, user} |
| **ForkNode** | `targets[]` | Launches all targets concurrently |
| **JoinNode** | `sources[]` | Barrier — waits for all sources |
| **Study** | Inherits FnNode + `focus` | Distinguished wrapper for `factory study` |
| **Edge** | `source`, `target`, `condition` | `condition` nullable; when set ∈ VerdictType |
| **Verdict** | `type`, `target`, `feedback`, `max_iterations`, `reason` | RELOOP MUST have target (model_validator); HALT MUST have reason |
| **Workflow** | `name`, `nodes`, `edges`, `start_node`, `terminal`, `trigger` | `terminal=True` prevents mode chaining |

### §6.5 Runtime Models

| Entity | Key Fields | Invariants |
|---|---|---|
| **AgentRunRequest** | `prompt`, `task`, `cwd`, `timeout`, `model`, `skip_permissions`, `role`, `extras` | `timeout` defaults 600.0; `extras` carries `tmux_persist`, `background` |
| **AgentRunResult** | `stdout`, `return_code`, `usage`, `metadata` | `usage` nullable (only Claude returns telemetry) |
| **AgentUsage** | `input_tokens`, `output_tokens`, `cache_read_tokens`, `total_cost_usd`, `duration_ms`, `num_turns`, `model` | All default 0 |
| **CycleState** | `cycle_id`, `started_at`, `mode`, `initial_prompt`, `respawns`, `runner_name` | `initial_prompt` truncated to ≤1000 chars; staleness at 24h |
| **CheckpointState** | `mode`, `active_experiment_id`, `completed_agents`, `pending_agents`, `last_eval_scores`, `current_hypothesis`, `completed_hypotheses` | `completed_hypotheses` defaults `[]` (backward compat) |
| **SessionSummary** | `project_name`, `mode`, `experiments_kept`, `experiments_reverted`, `score_start`, `score_end`, `total_cost_usd` | Strict model — rejects extra fields |
| **RunnerMeta** | `name`, `display_name`, `binary`, `install_hint`, `required_env_vars`, `custom_auth_check` | `is_available()` checks `shutil.which(binary)` |

### §6.6 Cross-Project Models

| Entity | Key Fields | Invariants |
|---|---|---|
| **ProjectEntry** | `path`, `name`, `registered_at`, `last_experiment_at`, `experiment_count`, `latest_score` | Global registry entry |
| **ProjectRegistry** | `projects[]`, `updated_at` | Persisted at `~/.factory/registry.json`; atomic save via `.tmp` rename |
| **PlaybookItem** | `id`, `content`, `helpful`, `harmful`, `section` | `net_score = helpful - harmful`; serialized as `[id] helpful=N harmful=M :: content` |
| **Playbook** | `role`, `items[]` | YAML frontmatter; items sorted by `net_score` descending within section |
| **PerformanceReport** | `project_name`, `total_experiments`, `keep_rate`, `agent_verdicts[]`, `observations[]`, `verdict_patterns` | Consolidated for ACE consumption |

---

## §7 State Machines and Lifecycles

### §7.1 Project State Detection

```
detect_state(path) →
  !exists or !.git               → NO_REPO
  eval_profile.json[human_reviewed=false] → EVALS_PENDING_REVIEW
  .factory/config.json exists    → HAS_FACTORY
  .git + open 'plan' issues      → REPO_INCOMPLETE
  .git, no open issues           → NO_FACTORY
```

The factory MUST check `EVALS_PENDING_REVIEW` before `HAS_FACTORY` to handle the discover → review → init flow. Missing `human_reviewed` key MUST default to pending review. Malformed `eval_profile.json` MUST fall through to `NO_FACTORY`. Only the `plan` label signals unbuilt repos — `implementation` label MUST NOT trigger `REPO_INCOMPLETE`.

### §7.2 Experiment Lifecycle

```
store.init() → store.begin(hypothesis) → [exp_id allocated, FileLock]
  → save_eval(exp_id, "before") → Builder implements
  → save_eval(exp_id, "after")  → save_diff(exp_id)
  → finalize(exp_id, record)    → [verdict.json + TSV append, FileLock]
  → registry.update_project_stats()
```

- `init()` MUST be idempotent — safe to call multiple times
- `begin()` MUST use `FileLock` for concurrent ID allocation
- `begin()` MUST NOT overwrite existing `hypothesis.md`
- `begin()` MUST register project in global registry (errors swallowed)
- `finalize()` MUST use `FileLock` for TSV append
- `finalize()` MUST auto-create experiment dir if deleted (crash resilience)
- `finalize()` MUST compute `delta = score_after - score_before` when `delta is None`
- `load_history()` MUST handle missing `research_citations` column (backward compat)
- Invalid verdict values MUST be coerced to `"error"`

### §7.3 Workflow Execution

```
WorkflowExecutor.execute() →
  _execute_from(start_node) →
    ForkNode  → asyncio.gather(branch_targets) → follow next
    JoinNode  → increment nodes_executed → follow next
    GateNode  → _evaluate_gate → Verdict:
      PROCEED → follow proceed edge
      RELOOP  → check iteration_counts[(gate_id, target)]
                if < max_iterations → inject feedback → _execute_from(target)
                if ≥ max_iterations → HALT
      HALT    → set halted=True, record reason
    AgentNode/FnNode/Study →
      if blocking: execute synchronously → follow next
      if non-blocking: asyncio.Task → follow next immediately
```

- The executor MUST track `iteration_counts` per `(gate_id, target)` pair
- Gate feedback MUST be accumulated in `node_context` across iterations
- Non-blocking nodes MUST run as `asyncio.Task`
- Node failure (exit 1) MUST halt workflow with "failed" reason
- Events emitted: `workflow.started`, `node.started`, `node.completed`, `gate.verdict`, `workflow.completed`, `workflow.halted`

### §7.4 CEO Completion Guard

```
run_with_completion_guard() →
  check existing cycle_state → restore mode + runner
  OR create new CycleState → persist to cycle.json
  → invoke CEO → check exit code
  → user interrupt (signal >128) → preserve cycle state, return
  → explicit ABORT event → delete cycle state, return
  → _detect_incomplete():
    improve/research/meta: verdict_count < hypothesis_count → incomplete
    build: phase_count < total_phases → incomplete
    discover: no eval_profile.json → incomplete
  → if incomplete: _build_continuation_task → respawn (max 5)
  → if cap hit: write cycle-incomplete.md, return error
```

- The guard MUST NOT respawn when `FACTORY_CEO_RESPAWN_DISABLED=1`
- `background=True` MUST bypass respawn loop entirely (single dispatch)
- Cycle state older than 24 hours MUST be treated as stale (return `None`)
- Mode MUST be preserved from initial cycle across all respawns
- Continuation tasks MUST include `## CRITICAL: Mode Override` section with `cycle_id`
- Each respawn MUST emit `ceo.respawn` event with `cycle_id` and `mode`
- `_count_verdicts` MUST use `since_ts` parameter to scope to current cycle only

### §7.5 Precheck Gate (Non-Overridable)

```
run_precheck() →
  1. check_score_direction  — no regression, meets threshold
  2. check_scope           — factory guard --check-scope (if baseline_sha)
  3. check_surfaces        — factory guard --check-surfaces (if baseline_sha + fixed_surfaces)
  4. check_anti_pattern    — hypothesis not similar to reverted experiments (Jaccard ≥ 0.6)
  5. check_hard_constraints — user-defined shell commands exit 0
  6. check_qa_execution    — QA agent was invoked (Sacred Rule 9)
  → ANY failure = mandatory revert; CEO MUST NOT override
```

- `check_score_direction`: `None` scores → MUST fail
- `check_qa_execution`: matches both old monolithic QA and new deep-QA specialist events
- `check_qa_execution`: MUST be skipped when `exp_id=None`
- When verdict is `keep` but precheck fails → override to `revert`, emit `verdict.overridden` event

### §7.6 FEEC Priority and Stuck/Plateau Detection

**Category classification** (keyword matching, checked in priority order):

| Priority | Category | Keywords |
|---|---|---|
| 0 (highest) | FIX | fix, error, bug, crash, fail, regression, broken, repair |
| 1 | EXPLOIT | improve, increase, extend, enhance, build on, optimize, boost |
| 2 | EXPLORE | (catch-all default — no keyword match) |
| 3 (lowest) | COMBINE | combine, merge, integrate, unify, consolidate |

**Stuck detection**: `detect_stuck(history, threshold=3)` — walks history backwards collecting consecutive reverts. Returns `True` when last `threshold` consecutive reverts share the same FEEC category. A `keep` verdict breaks the streak.

**Plateau detection** (two variants):
- `detect_research_plateau(run_summaries, threshold=3)`: requires `threshold + 1` entries; no improvement in last N cycles vs. best-before-window
- `detect_plateau(history, threshold=3)`: walks scored experiments tracking running best; plateau when `no_improvement_streak >= threshold`

### §7.7 Consecutive Agent Failure Tracking

```
invoke_agent() called →
  return_code == 0 → reset _consecutive_failures to 0
  return_code != 0 → increment _consecutive_failures
    _consecutive_failures >= 2 → emit cycle.aborted → raise ConsecutiveAgentFailureError
    _consecutive_failures < 2 → return (output, 1)
  exception → increment _consecutive_failures → return ("Error: ...", 1)
```

For parallel invocations: `invoke_agents_parallel` tracks failures locally. If ALL agents in a batch fail AND count ≥ 2 → raise `ConsecutiveAgentFailureError`.

### §7.8 ACE Pipeline (Playbook Evolution)

```
Reflect → scan experiments → compute category stats → _detect_repetition
  → generate candidate bullets per role (role-specific generators)
Curate → merge by dedup (SequenceMatcher) → sum counters
  → prune net-negative (harmful - helpful ≥ 3 AND observations ≥ 3)
  → cap at max_items → reassign sequential IDs
Inject → append "Behavioral Playbook" section to agent prompt at invocation
Persist → write to ~/.factory/playbooks/<role>.md (YAML frontmatter)
```

- `PlaybookItem.from_line()` MUST return `None` on invalid input
- Items MUST be sorted by `net_score` descending within each section (DO/DON'T)
- Roundtrip: `to_markdown()` ↔ `from_markdown()` MUST be lossless

### §7.9 Worktree Lifecycle

```
create_worktree(project, base_branch?, run_id?)
  → run_id truncated to 8 chars
  → git worktree add .factory-worktrees/run-{id}, branch factory/run-{id}
  → create .factory symlink to main project's .factory/
  → emit worktree.created event (errors swallowed)
remove_worktree(project, wt_path, branch)
  → remove directory + branch + git worktree entry
  → idempotent (safe to call twice)
  → emit worktree.removed event (errors swallowed)
prune_stale(project)
  → no-op without .factory-worktrees/
  → cleans orphaned directories not in git worktree list
  → preserves active worktrees
```

- `ExperimentStore` via worktree symlink MUST resolve to main `.factory/`
- Two concurrent `store.begin()` calls MUST get sequential IDs (filelock)

### §7.10 Runner Selection and Auth

```
get_runner(name=None, project_path=None)
  1. Explicit name argument
  2. FACTORY_RUNNER env var
  3. Default: "claude"
  Unknown name → ValueError("Unknown runner 'X'")
```

**Bob auth resolution**:
```
_check_auth(start_path):
  1. BOBSHELL_API_KEY env var → authenticated
  2. Walk up for .factory/.bob_auth → load into env
  3. ~/.bob/settings.json exists → native auth
  4. None → raise BobAuthError
```

**Codex auth resolution**:
```
_check_auth():
  1. ~/.codex/auth.json → OAuth (preferred)
  2. CODEX_API_KEY or OPENAI_API_KEY in env → API key mode
  3. None → raise CodexAuthError
  OAuth mode → strip OPENAI_API_KEY from env
  API key mode → set CODEX_HOME to temp dir (avoid stale OAuth)
```

**Bob ceiling enforcement**:
```
check_ceilings(project_path, cycle_start):
  count = count_cycle_invocations(project_path, cycle_start)
    → filters: timestamp > cycle_start AND dry_run=false
  count ≥ max → raise CeilingExceededError
  remaining ≤ 2 → return CeilingWarning
  otherwise → return None
```

---

## §8 Module Specifications

### §8.1 `factory/state.py` — Project State Detection

| Contract | Normative |
|---|---|
| `detect_state` returns one of 5 `ProjectState` values | MUST |
| Check `EVALS_PENDING_REVIEW` before `HAS_FACTORY` | MUST |
| Only `plan` label signals unbuilt repo (not `implementation`) | MUST |
| `_has_open_plan_issues` timeout at 15s | SHOULD |
| Graceful on `gh` CLI unavailable (returns `False`) | MUST |
| Malformed `eval_profile.json` falls through to `NO_FACTORY` | MUST |

### §8.2 `factory/store.py` — Experiment Store

| Contract | Normative |
|---|---|
| `init` creates `.factory/` with `experiments/`, `strategy/`, `agents/`, `reviews/`, `config.json`, `results.tsv` | MUST |
| `begin` uses `FileLock` for concurrent ID allocation | MUST |
| `begin` auto-registers project in global registry (errors swallowed) | MUST |
| `begin` MUST NOT overwrite existing `hypothesis.md` | MUST |
| `finalize` uses `FileLock` for TSV append | MUST |
| `finalize` computes delta when not pre-set | MUST |
| `finalize` auto-creates experiment dir if deleted | MUST |
| `load_history` handles missing `research_citations` column | MUST |
| `read_config` uses `strict=False` for enum coercion from JSON | MUST |
| `reparse_config` parses `factory.md` sections, HTML comments, code blocks, list continuations | MUST |
| `reparse_config`: incomplete research target → `None` (not crash) | MUST |
| `reparse_config`: negative/zero `test_timeout` → fallback to 600 | MUST |
| `ensure_factory_dir` removes broken/circular symlinks before mkdir | MUST |

### §8.3 `factory/eval/runner.py` — Eval Runner

| Contract | Normative |
|---|---|
| Compute 6 mandatory hygiene + 6 mandatory growth dimensions | MUST |
| Default weight split: 50% hygiene / 50% growth (no project eval) | MUST |
| With project eval (no explicit weights): 30% hygiene / 20% growth / 50% project | MUST |
| With explicit weights: normalize to sum 1.0 | MUST |
| `_normalize_tier` rescales weights to target sum, preserving scores/passed/details | MUST |
| Sparse within-tier overrides applied before normalization | SHOULD |
| Mandatory dimension names MUST NOT be overridden by project eval | MUST |
| `VIRTUAL_ENV` stripped from subprocess environment | MUST |
| Save results to `.factory/last_eval.json` | SHOULD |
| Auto-promote executable `eval_spec` items to project eval | SHOULD |

### §8.4 `factory/eval/scorer.py` — Composite Score

| Contract | Normative |
|---|---|
| Normalize weights if sum ≠ 1.0 (within 1e-9 tolerance) | MUST |
| `passed = (no guard_violations) ∧ (total ≥ threshold)` | MUST |
| Empty results → `total = 0.0`, passed only if `threshold ≤ 0.0` | MUST |

### §8.5 `factory/precheck.py` — Non-Overridable Gate

| Contract | Normative |
|---|---|
| A single failure makes the entire precheck fail | MUST |
| The CEO MUST NOT override a failed precheck | MUST |
| `check_score_direction`: `None` scores → fail | MUST |
| `check_anti_pattern`: Jaccard threshold default 0.6 | MUST |
| `check_qa_execution`: matches both monolithic QA and deep-QA specialist events | MUST |
| `check_qa_execution`: skipped when `exp_id=None` | MUST |
| `check_qa_execution`: no `experiment.begin` event → pass (skip check) | MUST |
| Hard constraint timeout: 120s default | SHOULD |

### §8.6 `factory/strategy.py` — FEEC Heuristic

| Contract | Normative |
|---|---|
| `categorize_hypothesis`: keyword match, FIX first, then EXPLOIT, then COMBINE, default EXPLORE | MUST |
| `rank_hypotheses`: stable sort by FEEC priority; injects `category` key | MUST |
| `detect_stuck`: True when N consecutive reverts share a FEEC category | MUST |
| `detect_plateau`: True when `no_improvement_streak ≥ threshold` among scored experiments | MUST |
| `detect_research_plateau`: requires `threshold + 1` entries; compares window best vs. pre-window best | MUST |
| `hypothesis_similarity`: Jaccard on tokens ≥ 3 chars | MUST |
| `format_tiered_history`: Tier 1 (last 3) full, Tier 2 (4-10) one-line, Tier 3 (11+) aggregate | MUST |
| `MAX_INLINE_HISTORY = 10` | MUST |

### §8.7 `factory/agents/runner.py` — Agent Runner

| Contract | Normative |
|---|---|
| Two-tier prompt lookup: project override (`.factory/agents/<role>.md`) → factory default | MUST |
| Auto-inject ACE playbook (even with project overrides) | MUST |
| Auto-inject user profile when `use_profile=True` | SHOULD |
| Append GitHub disabled directive when `FACTORY_NO_GITHUB=1` | MUST |
| Emit `agent.started`/`completed`/`failed` events | MUST |
| Consecutive failure threshold = 2 → raise `ConsecutiveAgentFailureError` | MUST |
| Emit `cycle.aborted` event before raising | MUST |
| Save agent output to `.factory/reviews/<role>[-<tag>]-latest.md` | MUST |
| Append `IDENTITY_REANCHOR` to non-CEO review files (Sacred Rule 8) | MUST |
| Auto-generate numeric review tags for duplicate roles in parallel invocations | MUST |
| Event emissions MUST be swallowed on error (never block agent invocation) | MUST |
| Telemetry spans MUST be swallowed on error | MUST |

### §8.8 `factory/workflow/primitives.py` — Workflow Primitives

| Contract | Normative |
|---|---|
| `Verdict` RELOOP requires `target` (model_validator) | MUST |
| `Verdict` HALT requires `reason` (model_validator) | MUST |
| `Workflow.validate_graph()` delegates to networkx validation | MUST |
| `Workflow.subgraph()` deep-copies nodes, filters edges to internal only | MUST |
| `Workflow.subgraph()`: missing node → `ValueError` | MUST |
| `Factory.select_workflow` returns first workflow whose trigger matches | MUST |
| `DEFAULT_AGENT_POOL`: 12 entries with role-specific model and timeout defaults | MUST |

### §8.9 `factory/workflow/definitions.py` — Workflow Definitions

| Contract | Normative |
|---|---|
| `register_all()` returns exactly 20 workflows | MUST |
| All workflows MUST pass `validate_graph()` | MUST |
| W₁ Build: trigger on `NO_REPO` or `REPO_INCOMPLETE` | MUST |
| W₂ Design: W₁ with user gate at strategy approval; trigger requires `interactive=True` | MUST |
| W₃ Improve: trigger on `HAS_FACTORY` | MUST |
| W₃b QA: subgraph of W₃; gate_qa HALT (not RELOOP to builder) | MUST |
| W₄ Research: extends W₃ with baseline, failure_analyst, plateau gate; trigger requires `research_target` | MUST |
| W₅ Meta: insights → playbook evolution → test pruning; archivist non-blocking | MUST |
| W₆ Discover: trigger on `NO_FACTORY` | MUST |
| W₇ Review: trigger on `EVALS_PENDING_REVIEW` | MUST |
| W₈ Refine: Tier 3 → HALT via `gate_tier` (fn evaluator) | MUST |
| W₉ Create: fork/join research → user gate → builder → deep-QA | MUST |
| Deep-QA subgraph: health_checker → code_reviewer → gate_review (CRITICAL_FOUND) → adversarial_tester | MUST |
| Doc freshness gate: present in build, improve, research, refine, create | MUST |
| Terminal workflows (`terminal=True`) MUST NOT trigger mode chaining | MUST |
| Every non-benchmark workflow with Builder MUST have deep-QA reachable | MUST |
| Contributed benchmarks (swebench, featurebench, terminalbench, legacybench): `terminal=True`, no factory eval, no deep-QA | MUST |

### §8.10 `factory/eval/guards.py` — Guard Rules

| Contract | Normative |
|---|---|
| `check_eval_immutable`: `eval/` directory MUST NOT be modified | MUST |
| `check_git_clean`: working tree MUST be clean (ignoring lock files like `uv.lock`) | MUST |
| `check_scope`: changed files MUST be within declared scope globs | MUST |
| `check_fixed_surfaces`: fixed surface files MUST NOT be modified (lock files ignored even with `**`) | MUST |
| `check_experiment_branch`: no commits since baseline → "No commits" violation | MUST |
| `_glob_match`: `**` matches across directory boundaries; `*` does not | MUST |

### §8.11 `factory/runners/` — Runner Abstraction

| Contract | Normative |
|---|---|
| Resolution order: explicit name → `FACTORY_RUNNER` env var → `"claude"` | MUST |
| Each runner implements `headless() → AgentRunResult` | MUST |
| Only Claude returns `usage` telemetry; others `usage=None` | MUST |
| Only Claude has `supports_background=True` | MUST |
| Bob Shell ceiling enforcement via `check_ceilings()` using cycle `started_at` | MUST |
| Bob ceiling uses `started_at` from `cycle.json`, not `now()` | MUST |
| Bob `sanitize=True` (strips ANSI from dest, keeps raw in buffer) | MUST |
| Claude sets `TELEMETRY_PLATFORM=''` to suppress native tracing | MUST |
| `VIRTUAL_ENV` stripped from all subprocess environments | MUST |
| Dry-run modes: `FACTORY_BOB_DRY_RUN`, `FACTORY_CODEX_DRY_RUN`, `FACTORY_OPENCODE_DRY_RUN` | MUST |
| Inactivity watchdog kills silent processes; genuine blank lines preserved | MUST |
| 1MB readline limit on subprocess output | SHOULD |
| Plugin discovery via `entry_points("factory.runners")` — lazy, once-per-process | SHOULD |

### §8.12 `factory/registry.py` — Global Project Registry

| Contract | Normative |
|---|---|
| Persisted at `~/.factory/registry.json` (overridable via `FACTORY_REGISTRY_DIR`) | MUST |
| Atomic save via `.tmp` rename | MUST |
| `register_project`: idempotent — skips if path already registered | MUST |
| `update_project_stats`: updates `last_experiment_at`, `experiment_count`, `latest_score` | MUST |
| Missing/corrupt registry → empty registry (no crash) | MUST |
| `get_project_paths`: stale entries (directory no longer exists) silently filtered | MUST |

### §8.13 `factory/spec/` — Behavioral Specification Engine

| Contract | Normative |
|---|---|
| `collect_source_files`: multi-language, excludes node_modules/.factory/__pycache__/.venv, respects `.gitignore` | MUST |
| `group_into_batches`: token-limited (80k), oversized files get own batch | MUST |
| `generate_spec`: parallel batch extraction (opus) → annotation → GRAPH-SPEC.md | MUST |
| No source files → `ValueError` | MUST |
| Agent nonzero exit → `RuntimeError` | MUST |
| `validate_spec` → (report, is_valid) via `_parse_verdict` | MUST |
| `_get_diff_text`: experiment diff → spec commit diff → HEAD~1 → --root (fallback chain) | MUST |

### §8.14 `factory/skill_cache.py` — Skill Cache

| Contract | Normative |
|---|---|
| `_compute_checksum`: SHA-256 of all workflow models; MUST sort sets for determinism | MUST |
| Cache at `~/.factory/cache/skills/{checksum}/` | MUST |
| Cache hit → copy workflow-* dirs to project | MUST |
| Cache miss → export → cache → copy; evict stale checksum dirs | MUST |
| Hand-written skills (non-workflow-*) MUST be preserved | MUST |

---

## §9 Shared Contracts

### §9.1 Event Protocol

All events MUST be appended to `.factory/events.jsonl` as newline-delimited JSON with fields: `type`, `timestamp` (ISO 8601), `project`, `agent` (nullable), `data` (dict).

Event types: `agent.started`, `agent.completed`, `agent.failed`, `agent.timeout`, `cycle.started`, `cycle.completed`, `cycle.aborted`, `ceo.respawn`, `ceo.message`, `experiment.begin`, `experiment.finalize`, `verdict.overridden`, `eval.started`, `eval.completed`, `worktree.created`, `worktree.removed`, `backlog.added`, `backlog.removed`, `bob.ceiling_warning`.

- `emit_event` MUST create `.factory/events.jsonl` and `.factory/` directory if absent
- `emit_event` MUST resolve symlinks before writing
- `load_events` supports `since` datetime filter; MUST skip blank lines
- Event emission exceptions MUST be swallowed silently (never block operations)

### §9.2 File I/O Contracts

- `ensure_factory_dir` MUST remove broken/circular symlinks before mkdir
- All file writes to `.factory/` SHOULD handle `OSError` gracefully
- Registry writes MUST use atomic `.tmp` rename
- Config files MUST be created with `0o600` permissions

### §9.3 Pydantic Model Contract

All domain models MUST use `ConfigDict(strict=True, extra="forbid")`. Extra fields MUST raise `ValidationError`. All models MUST support JSON roundtrip serialization.

### §9.4 Runner Protocol

All runners MUST implement:
```python
async def headless(request: AgentRunRequest) -> AgentRunResult
def interactive_run(request: AgentRunRequest) -> int
```

`RunnerMeta` describes capabilities: `is_available()` checks `shutil.which(binary)`; `check_auth()` validates credentials.

### §9.5 Notifier Protocol

```python
class Notifier(Protocol):
    async def send_digest(
        self, project_name: str,
        records: list[ExperimentRecord],
        composite: CompositeScore | None,
    ) -> None: ...
```

---

## §10 Configuration Specification

### §10.1 Five-Tier Precedence

```
CLI flag > env var > profile credential > config.toml [defaults] > hardcoded default
```

Empty/whitespace CLI values MUST be skipped (fall through to lower tiers).

### §10.2 Config File (`~/.factory/config.toml`)

```toml
[defaults]
runner = "claude"
projects_dir = "~/factory-projects"

[credentials.vertex]
FACTORY_RUNNER = "claude"
ANTHROPIC_API_KEY = "sk-ant-..."
```

- Profile names MUST match `[a-zA-Z0-9_-]+` (validated by `_validate_profile_name`)
- Credential keys MUST match `[A-Z_][A-Z0-9_]*` (validated by `_validate_credential_keys`)
- Config file MUST be created with `0o600` permissions
- Sensitive keys (containing "key", "token", "secret", "password") MUST be masked in `show_config`
- `migrate_env_to_config` MUST raise `FileExistsError` if config exists
- Profile not found → `KeyError`; file missing with profile → `FileNotFoundError`

### §10.3 Project Config (`factory.md` → `.factory/config.json`)

`ExperimentStore.reparse_config()` parses `factory.md` markdown into `FactoryConfig`. Section names mapped case-insensitively via `section_map` dict. Code blocks, HTML comments, and list continuations are handled.

---

## §11 Entry Points

| Entry Point | Mechanism | Purpose |
|---|---|---|
| `factory` CLI | `pyproject.toml` script → `factory.cli:main` | Primary user interface |
| `factory ceo /path` | CLI → completion guard → agent subprocess | Orchestrate improvement cycle |
| `factory run /path --loop` | Heartbeat wrapper (default interval 1800s) | Continuous improvement |
| `factory tmux /path --loop` | Detached tmux session | Background continuous improvement |
| `factory agent <role>` | CLI → `invoke_agent()` → runner subprocess | Direct specialist invocation |
| `factory workflow run <name>` | CLI → `WorkflowExecutor` | Headless DAG execution |
| `factory dashboard` | FastAPI server on :8420 | Web monitoring UI |

### §11.1 CLI Subcommand Groups

| Group | Commands |
|---|---|
| Entry Points | `ceo`, `run`, `tmux` |
| Project Setup | `detect`, `discover`, `init`, `eval` |
| Experiment Lifecycle | `begin`, `finalize`, `emit` |
| Project Intelligence | `study`, `diff`, `explain`, `insights` |
| Backlog & Refinement | `backlog-list`, `backlog-add`, `backlog-remove` |
| Knowledge & Archive | `export`, `backfill-archive` |
| Self-Evolution | `ace`, `ace-stats` |
| Configuration | `config show`, `config edit`, `config migrate` |
| Validation & Recovery | `checkpoint`, `resume`, `baseline`, `precheck`, `guard`, `review`, `spec` |

### §11.2 Mode Dispatch Rules

| Mode | Preconditions | Rejects |
|---|---|---|
| `build` | New project or idea | — |
| `design` | New or existing project, interactive | `--headless`, `--prompt` |
| `improve` | `HAS_FACTORY` | — |
| `research` | `HAS_FACTORY` + `research_target` | Existing without `research_target`; new + `--headless` |
| `review` | Existing directory + `--pr` | Missing `--pr` |
| `qa`/`deep-qa` | Existing directory + `--pr` | Missing `--pr` |
| `refine` | Existing directory | `--mode`, `--prompt`, `--focus` (mutually exclusive) |
| `create` | Any + `--focus` (mode description) | — |
| `auto` | Default; auto-detects | — |

---

## §12 Failure Model and Recovery

### §12.1 Error Types

| Error | Module | Trigger | Recovery |
|---|---|---|---|
| `ConsecutiveAgentFailureError` | `agents/runner.py` | 2+ consecutive failures | Abort cycle; emit `cycle.aborted`; check API keys |
| `ResultParseError` | `models.py` | Unparseable result: missing file, invalid JSON, non-numeric, NaN/Inf, zero denominator, unsupported parser | Return ERROR status |
| `BobAuthError` | `runners/bob.py` | No API key in env, file, or native config | Set `BOBSHELL_API_KEY` or `.factory/.bob_auth` |
| `CodexAuthError` | `runners/codex.py` | No API key and no OAuth credentials | Set `CODEX_API_KEY` or authenticate via OAuth |
| `OpenCodeAuthError` | `runners/opencode.py` | `OPENAI_API_KEY` unset and not sourceable | Set env var |
| `CeilingExceededError` | `runners/usage.py` | Invocations ≥ per-cycle max | Bump `FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE` |
| `FileNotFoundError` | `store.py` | Missing `config.json` | Run `factory init` |
| `ValueError` | `store.py` | Invalid JSON or schema mismatch | Run `factory init --reparse` |
| `ValueError` | `runners/__init__.py` | Unknown runner name | Use `claude`, `bob`, `codex`, or `opencode` |
| `FileNotFoundError` | `agents/runner.py` | Missing prompt file for role | Create `.factory/agents/<role>.md` or factory default |
| `ValueError("path traversal")` | `research/runner.py` | Cycle ID contains `..` or `/` | Use safe cycle IDs |

### §12.2 Recovery Patterns

| Scenario | Recovery |
|---|---|
| CEO premature exit | Completion guard detects incomplete work → auto-respawn (max 5) |
| Stale cycle state (>24h) | Ignored; fresh cycle created |
| Corrupt `cycle.json` | Returns `None` (no crash) |
| Corrupt `config.json` | Raises `ValueError` with "Run 'factory init --reparse'" message |
| Corrupt `results.tsv` | Invalid verdict values coerced to `"error"` |
| Missing `eval_profile.json` | Returns `None`; discovery mode triggered |
| Corrupt checkpoint | `load_checkpoint` returns `None` (no crash) |
| Missing experiment dir on finalize | Auto-created |
| Broken `.factory` symlink | `ensure_factory_dir` replaces with real directory |
| Worktree crash | `prune_stale()` cleans orphaned worktrees on next run |
| `gh` CLI unavailable | Graceful fallback (empty results, skipped checks) |
| Langfuse unavailable | Silent no-op; tracing disabled |
| Telegram send failure | Logged warning; returns without effect |
| Obsidian vault unconfigured | All write functions return `None`; no directories created |

---

## §13 Security and Safety

| Control | Implementation |
|---|---|
| API key isolation | Config file at `0o600` permissions; secrets masked in `show_config` |
| Fixed surface protection | Precheck gate blocks modifications to declared fixed surfaces |
| Scope enforcement | Guard checks restrict changes to declared scope patterns |
| Ground truth leakage detection | 3-check pipeline: token overlap (Jaccard), negation hints, specific values |
| Bob usage ceiling | Hard limit on invocations per cycle (`FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE`, default 8) |
| QA execution mandate | Sacred Rule 9: QA agent MUST be invoked for every experiment |
| CEO identity enforcement | Sacred Rule 8: identity re-anchor appended to all non-CEO review files |
| Path traversal prevention | `create_run_dir` rejects cycle IDs containing `..` or `/` |
| Environment isolation | `VIRTUAL_ENV` stripped; `TELEMETRY_PLATFORM` cleared; Codex OAuth strips API keys |
| Clean PR safety | `strip_pr_artifacts` stages only specific files; new files `git rm`'d, modified files `git checkout`'d; never includes untracked files |

---

## §14 Test and Validation Matrix

### §14.1 Key Behavioral Invariants

| # | Invariant | Enforcement |
|---|---|---|
| 1 | Eval 50/50 weight split (hygiene/growth) when no project eval | `_effective_weights` |
| 2 | Mandatory dimension names immutable — project eval cannot override | `_merge_all` name filtering |
| 3 | Neutral score = 0.5 for undetected tools/languages | hygiene evaluators |
| 4 | ACE pruning: `harmful - helpful ≥ 3` AND observations ≥ 3 | `curate_playbook` |
| 5 | Consecutive failure abort at threshold 2 | `_check_failure_threshold` |
| 6 | Cross-cycle isolation via `since_ts` parameter | `_count_verdicts` |
| 7 | `--bg` and `--bg-agents` mutually exclusive | `cmd_ceo` |
| 8 | CEO message filtering: only `type: "assistant"` with non-empty content | `_make_ceo_message_emitter` |
| 9 | Terminal workflows (`terminal=True`) don't chain | `_chain_modes` |
| 10 | Checkpoint backwards compat: missing `completed_hypotheses` → `[]` | `load_checkpoint` |
| 11 | Skill cache determinism: sets sorted before hashing | `_compute_checksum` |
| 12 | Clean PR safety: stages only specific files, never untracked | `strip_pr_artifacts` |
| 13 | Annotation-source fidelity: exported skills match source workflow graph | `validate_skill` |
| 14 | Broken symlink handling in `ensure_factory_dir` | store initialization |
| 15 | `register_all()` returns exactly 20 workflows; all pass `validate_graph()` | test_annotations.py |
| 16 | Tiered history: MAX_INLINE_HISTORY = 10 | `format_tiered_history` |
| 17 | Bob ceiling accumulates across invocations using `cycle.json` `started_at` | `check_ceilings` |
| 18 | ANSI sanitization: genuine blank lines preserved; redraw-only lines dropped | `_stream.py` |
| 19 | Review file convention: `<role>[-<tag>]-latest.md`; parallel auto-tags | `_save_review` |
| 20 | Config parsing: incomplete research target → `None` (not crash) | `reparse_config` |

### §14.2 Test Infrastructure

- Shared fixtures in `tests/conftest.py`: `tmp_project`, `sample_config`, `python_project`
- Autouse `_isolate_registry` fixture redirects global registry to temp directory
- `asyncio_mode = "auto"` — async test functions run without `@pytest.mark.asyncio`
- Dry-run modes: `FACTORY_BOB_DRY_RUN=1`, `FACTORY_CODEX_DRY_RUN=1`, `FACTORY_OPENCODE_DRY_RUN=1`

---

## §15 Extension Points

| Extension Point | Mechanism | Description |
|---|---|---|
| Custom runners | `factory.runners` entry point group | Register new CLI backends via `importlib.metadata` |
| Project agent overrides | `.factory/agents/<role>.md` | Per-project prompt customization; ACE playbook still injected |
| Custom workflows | `.factory/workflows/` or registered search paths | Project-specific workflow definitions; shadows built-ins |
| Hard constraints | `factory.md` `## Hard Constraints` section | User-defined shell checks enforced at precheck |
| Project eval dimensions | `factory.md` `## Project Eval` section | User-defined eval commands with name, command, parse, weight, timeout |
| Eval spec items | `factory.md` `## Eval Spec` section | Auto-promoted to project eval dimensions when executable |
| Within-tier weight overrides | `factory.md` `## Hygiene Weights` / `## Growth Weights` | Sparse weight adjustment per dimension |
| Playbook evolution | `~/.factory/playbooks/<role>.md` | ACE-evolved behavioral rules (user-local, persists across projects) |
| Obsidian vault | `FACTORY_VAULT_PATH` env var | Knowledge export destination (not `OBSIDIAN_VAULT_PATH`) |
| Notification backends | `Notifier` protocol | Currently: Telegram; extensible via protocol |

---

## §16 Implementation Checklist

### §16.1 Invariants That MUST Hold

- [ ] All Pydantic models use `ConfigDict(strict=True, extra="forbid")`
- [ ] `ExperimentStore` uses `FileLock` for `begin()` and `finalize()`
- [ ] Precheck gate is non-overridable by the CEO agent (implemented as `GateNode(evaluator_type="fn")`)
- [ ] All 20 workflows validate cleanly via `validate_graph()`
- [ ] Weight sums: default hygiene 50% + growth 50% = 100%
- [ ] FEEC priority order: FIX(0) < EXPLOIT(1) < EXPLORE(2) < COMBINE(3)
- [ ] Consecutive agent failure threshold = 2
- [ ] Max CEO respawns = 5 (configurable via `FACTORY_CEO_MAX_RESPAWNS`)
- [ ] Cycle staleness threshold = 24 hours
- [ ] Anti-pattern Jaccard similarity threshold = 0.6
- [ ] Tiered history: MAX_INLINE_HISTORY = 10
- [ ] `detect_state` checks EVALS_PENDING_REVIEW before HAS_FACTORY

### §16.2 Workflow-Specific Invariants

- [ ] W₁ Build: Phase 1 MUST be scaffold + eval harness
- [ ] W₂ Design: gate_strategy MUST be user evaluator
- [ ] W₃b QA: gate_qa MUST HALT (not RELOOP to builder) on failure
- [ ] W₄ Research: code_reviewer extra MUST verify mutable/fixed surface compliance
- [ ] W₅ Meta: Archivist MUST be non-blocking; test chain proceeds immediately
- [ ] W₈ Refine: Tier 3 MUST halt early via `gate_tier` (fn evaluator)
- [ ] W₁₀ Skill Refine: guard max 2 reloops, then fallback to unrefined output
- [ ] Doc freshness gate: present in build, improve, research, refine, create (5 workflows)
- [ ] Deep-QA subgraph: gate_review checks CRITICAL_FOUND via grep, not agent judgment
- [ ] Every non-benchmark workflow with Builder MUST have deep-QA specialist reachable

---

## Appendix A: Reference Algorithms

### A.1 FEEC Hypothesis Categorization

```python
def categorize_hypothesis(text: str) -> FEECCategory:
    lower = text.lower()
    if any(kw in lower for kw in ["fix","error","bug","crash","fail","regression","broken","repair"]):
        return FEECCategory.FIX
    if any(kw in lower for kw in ["improve","increase","extend","enhance","build on","optimize","boost"]):
        return FEECCategory.EXPLOIT
    if any(kw in lower for kw in ["combine","merge","integrate","unify","consolidate"]):
        return FEECCategory.COMBINE
    return FEECCategory.EXPLORE
```

### A.2 Hypothesis Similarity (Jaccard)

```python
def hypothesis_similarity(a: str, b: str) -> float:
    tokens_a = {w for w in a.lower().split() if len(w) >= 3}
    tokens_b = {w for w in b.lower().split() if len(w) >= 3}
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
```

### A.3 Weight Normalization

```python
def _normalize_tier(results, target_weight, overrides=None):
    if overrides:
        results = [r.copy(weight=overrides.get(r.name, r.weight)) for r in results]
    weight_sum = sum(r.weight for r in results)
    if weight_sum <= 0:
        return results
    return [r.copy(weight=(r.weight / weight_sum) * target_weight) for r in results]
```

### A.4 Composite Score

```python
def compute_composite(results, guard_violations, threshold):
    weight_sum = sum(r.weight for r in results)
    if weight_sum > 0 and abs(weight_sum - 1.0) > 1e-9:
        results = [r.copy(weight=r.weight / weight_sum) for r in results]
    total = sum(r.score * r.weight for r in results)
    passed = len(guard_violations) == 0 and total >= threshold
    return CompositeScore(total=total, results=results, guard_violations=guard_violations, passed=passed)
```

### A.5 Plateau Detection

```python
def detect_plateau(history, threshold=3):
    scored = [r for r in history if r.score_after is not None]
    if len(scored) < threshold:
        return False
    best = scored[0].score_after
    streak = 0
    for r in scored[1:]:
        if r.score_after > best:
            best = r.score_after
            streak = 0
        else:
            streak += 1
    return streak >= threshold
```

### A.6 Stuck Detection

```python
def detect_stuck(history, threshold=3):
    consecutive_reverts = []
    for entry in reversed(history):
        if entry["verdict"] != "revert":
            break
        consecutive_reverts.append(categorize_hypothesis(entry["hypothesis"]))
    if len(consecutive_reverts) < threshold:
        return False
    return len(set(consecutive_reverts[:threshold])) == 1
```

### A.7 Skill Cache Checksum

```python
def _compute_checksum(workflows):
    # Sort sets before hashing to avoid Python set-ordering nondeterminism
    data = sorted(serialize(workflow_models))
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()
    # Cache path: ~/.factory/cache/skills/{checksum}/
    # On miss: export → cache → copy; evict sibling dirs
```
